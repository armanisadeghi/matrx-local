//! THE APPKIT TERMINATION GUARD — every macOS quit ends in our bypass, not in
//! libc's static destructors.
//!
//! WHY (SR-01, still crashing on 1.4.115; measured from
//! `~/Library/Logs/DiagnosticReports/aimatrx-desktop-2026-09-15-080718.ips` and
//! `…-2026-09-15-023512.ips`):
//!
//! ```text
//! abort ← ggml_abort ← ggml_metal_rsets_free ← ggml_metal_device_free
//!       ← ~unique_ptr<ggml_metal_device> ← __cxa_finalize_ranges
//!       ← exit ← -[NSApplication terminate:] ← -[NSMenuItem _corePerformAction:]
//! ```
//!
//! The app links GGML in-process (`whisper-cpp-plus` with the `metal` feature),
//! and its Metal device is owned by a function-local static whose destructor
//! `__cxa_finalize_ranges` runs at `exit()`. That destructor calls
//! `ggml_abort()`, so every intentional quit is recorded by macOS as a crash.
//!
//! `lib.rs` already bypasses those destructors with `libc::_exit(0)` — but only
//! on tauri's `RunEvent::ExitRequested`. The stack above has NO tao or tauri
//! frame between `terminate:` and `exit`: AppKit ran libc's exit itself, from a
//! menu-bar Quit, and the event loop never saw the quit at all. Fixing the exit
//! *code* branch was never the whole class.
//!
//! So the interception moves to the one place EVERY macOS quit passes through —
//! the menu-bar Quit, ⌘Q, the Dock's Quit, an AppleEvent `quit` (which is how an
//! updater relaunch and `osascript` arrive), and logout/restart all send
//! `-[NSApplication terminate:]`. Swizzling it means our handler runs first and
//! never returns into AppKit's `exit()`: it starts graceful shutdown on a
//! background thread (the main thread must stay answerable to macOS's
//! NSApplication watchdog) and ends the process with `_exit(0)`.
//!
//! An `atexit` handler would NOT do: libc runs them in reverse registration
//! order, and GGML's device is registered lazily on first use — after anything
//! we could register at startup — so GGML's abort would still go first.

use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::OnceLock;

use objc2::runtime::{AnyClass, AnyObject, Imp, Sel};
use objc2::sel;

/// What to run when a quit arrives. It must end the process itself.
type TerminateHook = Box<dyn Fn() + Send + Sync + 'static>;

static HOOK: OnceLock<TerminateHook> = OnceLock::new();
/// The IMP we installed, so a guard test can prove the interception is live.
static INSTALLED_IMP: AtomicUsize = AtomicUsize::new(0);
/// A second Quit press while cleanup runs must not start a second cleanup.
static TERMINATING: AtomicBool = AtomicBool::new(false);

/// Our replacement for `-[NSApplication terminate:]`.
///
/// It deliberately does NOT call through to the original: returning without it
/// is exactly what `api.prevent_exit()` does on the tauri path — AppKit
/// abandons this termination, and the process ends from our own thread once
/// cleanup is done.
unsafe extern "C-unwind" fn matrx_terminate(
    _this: *mut AnyObject,
    _cmd: Sel,
    _sender: *mut AnyObject,
) {
    if TERMINATING.swap(true, Ordering::SeqCst) {
        return;
    }
    std::thread::spawn(|| {
        // `HOOK` is set once, before the swizzle goes in, so this cannot race
        // with a write. A missing hook still ends the process: returning would
        // leave a quit the person asked for unanswered.
        if let Some(hook) = HOOK.get() {
            hook();
        }
        unsafe { libc::_exit(0) };
    });
}

/// Install the guard. Idempotent; safe to call once during `setup`.
///
/// Returns an error string when the interception could not be installed, so
/// the caller can log it loudly rather than ship a silently unguarded quit.
pub fn install(hook: TerminateHook) -> Result<(), String> {
    if HOOK.set(hook).is_err() {
        return Ok(());
    }
    let class = AnyClass::get(c"NSApplication")
        .ok_or_else(|| "NSApplication is not registered with the Objective-C runtime".to_string())?;
    let method = class
        .instance_method(sel!(terminate:))
        .ok_or_else(|| "-[NSApplication terminate:] not found".to_string())?;
    let ours: Imp = unsafe {
        std::mem::transmute::<
            unsafe extern "C-unwind" fn(*mut AnyObject, Sel, *mut AnyObject),
            Imp,
        >(matrx_terminate)
    };
    // SAFETY: the signature matches `-[NSApplication terminate:]`
    // (`v@:@`), and the replacement dereferences none of its arguments.
    unsafe { method.set_implementation(ours) };
    INSTALLED_IMP.store(ours as usize, Ordering::SeqCst);
    Ok(())
}

/// Is a macOS quit actually intercepted right now?
///
/// Reads the LIVE class, not our own bookkeeping: it answers true only when
/// `-[NSApplication terminate:]` currently dispatches to the function above.
pub fn terminate_is_intercepted() -> bool {
    let installed = INSTALLED_IMP.load(Ordering::SeqCst);
    if installed == 0 {
        return false;
    }
    let Some(class) = AnyClass::get(c"NSApplication") else {
        return false;
    };
    let Some(method) = class.instance_method(sel!(terminate:)) else {
        return false;
    };
    method.implementation() as usize == installed
}

#[cfg(test)]
mod tests {
    use super::*;

    /// WHAT BREAKS THIS TEST: removing `install()`'s swizzle, misnaming the
    /// selector (`terminate` instead of `terminate:`), or targeting a class
    /// other than NSApplication — i.e. every way the guard could be installed
    /// on a path the crashing shutdown does not take, which is exactly what
    /// SR-01 was.
    #[test]
    fn appkit_quit_is_intercepted_only_after_the_guard_is_installed() {
        // AppKit's own terminate: is what the 1.4.115 crash ran. Before the
        // install, nothing of ours is in that slot.
        assert!(
            !terminate_is_intercepted(),
            "nothing should be intercepting terminate: before install()"
        );
        let original = AnyClass::get(c"NSApplication")
            .expect("AppKit must be linked into this test binary")
            .instance_method(sel!(terminate:))
            .expect("-[NSApplication terminate:] must exist")
            .implementation() as usize;

        install(Box::new(|| {})).expect("the guard must install");

        assert!(
            terminate_is_intercepted(),
            "-[NSApplication terminate:] must dispatch to our bypass after install()"
        );
        let after = AnyClass::get(c"NSApplication")
            .unwrap()
            .instance_method(sel!(terminate:))
            .unwrap()
            .implementation() as usize;
        assert_ne!(
            original, after,
            "AppKit's implementation must no longer be the one a quit reaches"
        );

        // Idempotent: a second call must not restore AppKit's implementation.
        install(Box::new(|| {})).expect("a second install must be a no-op");
        assert!(terminate_is_intercepted());
    }
}
