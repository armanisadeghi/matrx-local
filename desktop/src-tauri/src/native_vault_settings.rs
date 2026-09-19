//! Host-only, non-secret setup for the bundled AutoFill provider.
//!
//! This module deliberately reads only the credential-identity store's enabled
//! bit. It never asks the provider for identities, credentials, tokens, or
//! private-session state.

use serde::Serialize;

#[derive(Clone, Serialize)]
pub struct Status {
    pub supported: bool,
    pub artifact: &'static str,
    pub os_enablement: &'static str,
    pub enrollment: &'static str,
    pub signing_profile: &'static str,
    pub ready: bool,
    pub message: String,
    pub state: &'static str,
    pub last_configured_subject: Option<String>,
    pub enable_action: &'static str,
    pub settings_action: &'static str,
}

#[derive(Serialize)]
pub struct ActionResult {
    pub outcome: &'static str,
    pub message: String,
}

#[cfg(target_os = "macos")]
mod platform {
    use super::*;
    use block2::RcBlock;
    use objc2::{rc::autoreleasepool, runtime::AnyClass, sel};
    use objc2_authentication_services::{
        ASCredentialIdentityStore, ASCredentialIdentityStoreState, ASSettingsHelper,
    };
    use objc2_foundation::{NSBundle, NSError, NSOperatingSystemVersion, NSProcessInfo, NSString};
    use std::{
        ffi::c_void,
        path::PathBuf,
        sync::{Arc, Mutex, OnceLock},
        time::{Duration, Instant},
    };
    use tokio::sync::oneshot;

    const HOST_ID: &str = "com.aimatrx.desktop";
    const PROVIDER_ID: &str = "com.aimatrx.desktop.vault-provider";
    const PROVIDER_NAME: &str = "AI Matrx Vault Provider.appex";
    pub(super) const ENABLE_COOLDOWN: Duration = Duration::from_secs(10);
    const READ_WAIT: Duration = Duration::from_secs(8);
    const PROMPT_WAIT: Duration = Duration::from_secs(120);

    // Security.framework does not yet expose SecTask in the maintained objc2
    // crate. Keep this narrow C boundary here instead of placing selectors or
    // entitlement plumbing in lib.rs.
    #[link(name = "Security", kind = "framework")]
    unsafe extern "C" {
        fn SecTaskCreateFromSelf(allocator: *const c_void) -> *const c_void;
        fn SecTaskCopyValueForEntitlement(
            task: *const c_void,
            entitlement: *const c_void,
            error: *mut *const c_void,
        ) -> *const c_void;
        fn CFBooleanGetValue(value: *const c_void) -> bool;
        fn CFBooleanGetTypeID() -> usize;
        fn CFGetTypeID(value: *const c_void) -> usize;
        fn CFRelease(value: *const c_void);
    }

    #[derive(Default)]
    pub(super) struct EnableOperation {
        pending: bool,
        invoked_at: Option<Instant>,
    }
    impl EnableOperation {
        pub(super) fn claim(&mut self, now: Instant) -> Result<(), &'static str> {
            if self.pending {
                return Err("pending");
            }
            if self
                .invoked_at
                .is_some_and(|started| now.duration_since(started) < ENABLE_COOLDOWN)
            {
                return Err("cooldown");
            }
            // A completed prior request may leave an old invocation time behind.
            // This claim is only scheduled; it becomes invoked on the main thread.
            self.invoked_at = None;
            self.pending = true;
            Ok(())
        }
        pub(super) fn invoked(&mut self, now: Instant) {
            self.invoked_at = Some(now);
        }
        pub(super) fn complete(&mut self) {
            self.pending = false;
        }
        pub(super) fn dispatch_failed(&mut self) {
            // A claim is made before queueing to prevent duplicate prompts. Only
            // a queueing failure may release it; once invoked, its callback owns
            // release even if the command caller has timed out or disconnected.
            if self.invoked_at.is_none() {
                self.pending = false;
            }
        }
    }

    pub(super) struct EnableCompletion {
        operation: Arc<Mutex<EnableOperation>>,
        sender: Mutex<Option<oneshot::Sender<bool>>>,
    }
    impl EnableCompletion {
        pub(super) fn new(operation: Arc<Mutex<EnableOperation>>, sender: oneshot::Sender<bool>) -> Self {
            Self { operation, sender: Mutex::new(Some(sender)) }
        }
        pub(super) fn complete(&self, enabled: bool) {
            self.operation.lock().expect("enable operation lock").complete();
            if let Some(sender) = self.sender.lock().ok().and_then(|mut guard| guard.take()) {
                // The receiver is allowed to be gone after a command timeout.
                let _ = sender.send(enabled);
            }
        }
    }
    static ENABLE_OPERATION: OnceLock<Arc<Mutex<EnableOperation>>> = OnceLock::new();
    fn enable_operation() -> &'static Arc<Mutex<EnableOperation>> {
        ENABLE_OPERATION.get_or_init(|| Arc::new(Mutex::new(EnableOperation::default())))
    }

    fn string(value: &NSString) -> String {
        autoreleasepool(|pool| unsafe { value.to_str(pool) }.to_owned())
    }

    fn has_autofill_entitlement() -> bool {
        let name = NSString::from_str(
            "com.apple.developer.authentication-services.autofill-credential-provider",
        );
        unsafe {
            let task = SecTaskCreateFromSelf(std::ptr::null());
            if task.is_null() {
                return false;
            }
            let value = SecTaskCopyValueForEntitlement(
                task,
                objc2::rc::Retained::as_ptr(&name).cast(),
                std::ptr::null_mut(),
            );
            CFRelease(task);
            if value.is_null() {
                return false;
            }
            let enabled = CFGetTypeID(value) == CFBooleanGetTypeID() && CFBooleanGetValue(value);
            CFRelease(value);
            enabled
        }
    }

    fn host_artifact() -> Result<PathBuf, &'static str> {
        let bundle = NSBundle::mainBundle();
        let id = bundle.bundleIdentifier().map(|value| string(&value));
        if id.as_deref() != Some(HOST_ID) {
            return Err("This running executable is not the AI Matrx Desktop app bundle.");
        }
        let root = PathBuf::from(string(&bundle.bundlePath()));
        let executable = bundle
            .executablePath()
            .map(|value| PathBuf::from(string(&value)));
        let expected_executable_parent = root.join("Contents").join("MacOS");
        if !root.extension().is_some_and(|extension| extension == "app")
            || !executable
                .as_ref()
                .is_some_and(|path| path.starts_with(&expected_executable_parent))
        {
            return Err("This build is not running from its own packaged app bundle.");
        }
        let running = std::env::current_exe()
            .ok()
            .and_then(|path| std::fs::canonicalize(path).ok());
        let bundled = executable.and_then(|path| std::fs::canonicalize(path).ok());
        if running.is_none() || running != bundled {
            return Err("The running executable does not match this app bundle’s executable.");
        }
        let provider = root.join("Contents").join("PlugIns").join(PROVIDER_NAME);
        if !provider.is_dir() {
            return Err("This app bundle does not contain the AI Matrx Vault provider.");
        }
        let info = provider.join("Contents").join("Info.plist");
        let provider_id = plist::Value::from_file(&info).ok().and_then(|value| {
            value
                .as_dictionary()
                .and_then(|dictionary| dictionary.get("CFBundleIdentifier"))
                .and_then(plist::Value::as_string)
                .map(str::to_owned)
        });
        if provider_id.as_deref() != Some(PROVIDER_ID) {
            return Err("The bundled provider identifier does not match this desktop app.");
        }
        if !has_autofill_entitlement() {
            return Err(
                "This desktop app is missing the AutoFill credential-provider entitlement.",
            );
        }
        Ok(provider)
    }

    fn bundled_provider_present() -> bool {
        let root = PathBuf::from(string(&NSBundle::mainBundle().bundlePath()));
        root.join("Contents").join("PlugIns").join(PROVIDER_NAME).is_dir()
    }

    fn supports_provider_os() -> bool {
        NSProcessInfo::processInfo().isOperatingSystemAtLeastVersion(NSOperatingSystemVersion {
            majorVersion: 15,
            minorVersion: 0,
            patchVersion: 0,
        })
    }

    fn selector_available(selector: objc2::runtime::Sel) -> bool {
        AnyClass::get(c"ASSettingsHelper").is_some_and(|class| class.responds_to(selector))
    }
    fn identity_store_available() -> bool {
        AnyClass::get(c"ASCredentialIdentityStore")
            .is_some_and(|class| class.responds_to(sel!(sharedStore)))
    }
    fn settings_available() -> bool {
        selector_available(sel!(openCredentialProviderAppSettingsWithCompletionHandler:))
    }
    fn enable_available() -> bool {
        selector_available(sel!(requestToTurnOnCredentialProviderExtensionWithCompletionHandler:))
    }

    fn base_status() -> Status {
        let historical = crate::native_vault::status();
        let artifact = if bundled_provider_present() { "built" } else { "not_built" };
        if !supports_provider_os() {
            return unavailable_status(
                historical,
                artifact,
                "Native Vault AutoFill requires macOS 15 or later.",
            );
        }
        match host_artifact() {
            Ok(_) if identity_store_available() => Status {
                supported: true, artifact: "built", os_enablement: "unavailable",
                enrollment: historical.state, signing_profile: "not_verified", ready: false,
                message: "Checking this app’s current macOS AutoFill state. A configured Vault record does not establish a live session or filling authority.".into(),
                state: historical.state, last_configured_subject: historical.last_configured_subject,
                enable_action: if enable_available() { "available" } else { "not_supported" },
                settings_action: if settings_available() { "available" } else { "not_supported" },
            },
            Ok(_) => unavailable_status(historical, artifact, "This macOS version cannot read credential-provider enablement."),
            Err(reason) => unavailable_status(historical, artifact, reason),
        }
    }
    fn unavailable_status(
        historical: crate::native_vault::HistoricalStatus,
        artifact: &'static str,
        reason: &str,
    ) -> Status {
        Status {
            supported: false,
            artifact,
            os_enablement: "unavailable",
            enrollment: historical.state,
            signing_profile: "not_verified",
            ready: false,
            message: reason.into(),
            state: historical.state,
            last_configured_subject: historical.last_configured_subject,
            enable_action: "unavailable",
            settings_action: "unavailable",
        }
    }

    async fn read_enabled(app: &tauri::AppHandle) -> Result<bool, &'static str> {
        if !identity_store_available() {
            return Err("not_supported");
        }
        let (tx, rx) = oneshot::channel::<bool>();
        let completion = Arc::new(Mutex::new(Some(tx)));
        app.run_on_main_thread(move || {
            let store = unsafe { ASCredentialIdentityStore::sharedStore() };
            let callback_completion = completion.clone();
            let callback = RcBlock::new(
                move |state: std::ptr::NonNull<ASCredentialIdentityStoreState>| {
                    if let Some(tx) = callback_completion
                        .lock()
                        .ok()
                        .and_then(|mut guard| guard.take())
                    {
                        let _ = tx.send(unsafe { state.as_ref().isEnabled() });
                    }
                },
            );
            unsafe {
                store.getCredentialIdentityStoreStateWithCompletion(&callback);
            }
        })
        .map_err(|_| "unavailable")?;
        match tokio::time::timeout(READ_WAIT, rx).await {
            Ok(Ok(enabled)) => Ok(enabled),
            _ => Err("unavailable"),
        }
    }

    pub async fn status(app: tauri::AppHandle) -> Status {
        let mut status = base_status();
        if !status.supported {
            return status;
        }
        match read_enabled(&app).await {
            Ok(true) => {
                status.os_enablement = "enabled";
                status.message = "macOS has enabled AutoFill for this packaged provider. This does not verify a live Vault session or credential filling.".into();
            }
            Ok(false) => {
                status.os_enablement = "disabled";
                status.message = "macOS AutoFill is off for this packaged provider. Enable it or open macOS settings.".into();
            }
            Err("not_supported") => {
                status.os_enablement = "not_supported";
                status.message = "This macOS version cannot read AutoFill provider state.".into();
            }
            Err(_) => {
                status.os_enablement = "unavailable";
                status.message = "macOS did not return the current AutoFill state. Try Refresh; Settings remains available when supported.".into();
            }
        }
        status
    }

    pub async fn open_settings(app: tauri::AppHandle) -> ActionResult {
        let status = base_status();
        if !status.supported || !settings_available() {
            return ActionResult {
                outcome: "unavailable",
                message: status.message,
            };
        }
        let (tx, rx) = oneshot::channel::<bool>();
        let completion = Arc::new(Mutex::new(Some(tx)));
        if app
            .run_on_main_thread(move || {
                let callback_completion = completion.clone();
                let callback = RcBlock::new(move |error: *mut NSError| {
                    if let Some(tx) = callback_completion
                        .lock()
                        .ok()
                        .and_then(|mut guard| guard.take())
                    {
                        let _ = tx.send(error.is_null());
                    }
                });
                unsafe {
                    ASSettingsHelper::openCredentialProviderAppSettingsWithCompletionHandler(Some(
                        &callback,
                    ));
                }
            })
            .is_err()
        {
            return ActionResult {
                outcome: "unavailable",
                message: "AI Matrx Desktop could not reach macOS’s main thread.".into(),
            };
        }
        match tokio::time::timeout(READ_WAIT, rx).await {
            Ok(Ok(true)) => ActionResult { outcome: "opened", message: "macOS AutoFill settings opened. Opening Settings does not confirm that AutoFill is enabled.".into() },
            Ok(Ok(false)) => ActionResult { outcome: "failed", message: "macOS could not open AutoFill settings.".into() },
            _ => ActionResult { outcome: "pending", message: "macOS has not confirmed Settings navigation yet.".into() },
        }
    }

    pub async fn request_enable(app: tauri::AppHandle) -> ActionResult {
        let status = base_status();
        if !status.supported || !enable_available() {
            return ActionResult {
                outcome: "unavailable",
                message: status.message,
            };
        }
        {
            let mut operation = enable_operation().lock().expect("enable operation lock");
            match operation.claim(Instant::now()) {
                Err("pending") => return ActionResult { outcome: "pending", message: "macOS is still handling the earlier AutoFill request. You can open Settings while it is pending.".into() },
                Err(_) => return ActionResult { outcome: "cooldown", message: "macOS requires ten seconds between AutoFill enable requests.".into() },
                Ok(()) => {}
            }
        }
        let (tx, rx) = oneshot::channel::<bool>();
        let completion = Arc::new(EnableCompletion::new(Arc::clone(enable_operation()), tx));
        let dispatch = app.run_on_main_thread(move || {
            enable_operation()
                .lock()
                .expect("enable operation lock")
                .invoked(Instant::now());
            let callback_completion = completion.clone();
            let callback = RcBlock::new(move |enabled: objc2::runtime::Bool| {
                callback_completion.complete(enabled.as_bool());
            });
            unsafe {
                ASSettingsHelper::requestToTurnOnCredentialProviderExtensionWithCompletionHandler(
                    &callback,
                );
            }
        });
        if dispatch.is_err() {
            enable_operation()
                .lock()
                .expect("enable operation lock")
                .dispatch_failed();
            return ActionResult {
                outcome: "unavailable",
                message: "AI Matrx Desktop could not reach macOS’s main thread.".into(),
            };
        }
        match tokio::time::timeout(PROMPT_WAIT, rx).await {
            Ok(Ok(true)) => ActionResult { outcome: "enabled", message: "macOS enabled AutoFill. Refreshing status will confirm the current state.".into() },
            Ok(Ok(false)) => ActionResult { outcome: "disabled", message: "AutoFill is still off in macOS.".into() },
            _ => ActionResult { outcome: "pending", message: "macOS has not completed the AutoFill request. You can open Settings while it is pending.".into() },
        }
    }
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::platform::{EnableCompletion, EnableOperation, ENABLE_COOLDOWN};
    use std::{sync::{Arc, Mutex}, time::{Duration, Instant}};
    use tokio::sync::oneshot;

    #[test]
    fn enable_admission_is_single_owner_and_cooldown_starts_on_invocation() {
        let now = Instant::now();
        let mut state = EnableOperation::default();
        assert_eq!(state.claim(now), Ok(()));
        assert_eq!(state.claim(now), Err("pending")); // simultaneous request
        state.invoked(now + Duration::from_secs(1));
        state.complete(); // callback after a caller timeout/cancellation
        assert_eq!(
            state.claim(now + Duration::from_secs(1) + ENABLE_COOLDOWN - Duration::from_millis(1)),
            Err("cooldown")
        );
        assert_eq!(
            state.claim(now + Duration::from_secs(1) + ENABLE_COOLDOWN),
            Ok(())
        );
    }

    #[test]
    fn scheduled_request_survives_a_missing_callback_until_a_callback_completes_it() {
        let now = Instant::now();
        let mut state = EnableOperation::default();
        assert_eq!(state.claim(now), Ok(())); // enqueue succeeded
        assert_eq!(state.claim(now + Duration::from_secs(121)), Err("pending")); // waiter timed out, no callback
        state.complete(); // late callback is the only completion owner
        assert_eq!(state.claim(now + Duration::from_secs(121)), Ok(()));
    }

    #[test]
    fn queue_failure_releases_a_scheduled_request_without_starting_cooldown() {
        let now = Instant::now();
        let mut state = EnableOperation::default();
        assert_eq!(state.claim(now), Ok(()));
        state.dispatch_failed();
        assert_eq!(state.claim(now), Ok(()));
    }

    #[test]
    fn a_new_claim_after_cooldown_can_be_released_if_queueing_fails() {
        let now = Instant::now();
        let mut state = EnableOperation::default();
        state.claim(now).unwrap();
        state.invoked(now);
        state.complete();
        let later = now + ENABLE_COOLDOWN;
        state.claim(later).unwrap();
        state.dispatch_failed();
        assert_eq!(state.claim(later), Ok(()));
    }

    #[test]
    fn dispatch_failure_cannot_release_an_invoked_request() {
        let now = Instant::now();
        let mut state = EnableOperation::default();
        assert_eq!(state.claim(now), Ok(()));
        state.invoked(now);
        state.dispatch_failed();
        assert_eq!(state.claim(now + Duration::from_secs(121)), Err("pending"));
    }

    #[test]
    fn owned_callback_clears_state_after_caller_cancellation() {
        let now = Instant::now();
        let operation = Arc::new(Mutex::new(EnableOperation::default()));
        operation.lock().unwrap().claim(now).unwrap();
        operation.lock().unwrap().invoked(now);
        let (sender, receiver) = oneshot::channel();
        drop(receiver); // The command caller went away before macOS replied.
        EnableCompletion::new(Arc::clone(&operation), sender).complete(true);
        assert_eq!(operation.lock().unwrap().claim(now + ENABLE_COOLDOWN), Ok(()));
    }
}

#[cfg(target_os = "macos")]
pub use platform::{open_settings, request_enable, status};

#[cfg(not(target_os = "macos"))]
pub async fn status(_: tauri::AppHandle) -> Status {
    Status {
        supported: false,
        artifact: "not_supported",
        os_enablement: "not_supported",
        enrollment: "not_supported",
        signing_profile: "not_supported",
        ready: false,
        message: "Native Vault AutoFill is currently available only in AI Matrx Desktop on macOS."
            .into(),
        state: "unsupported_platform",
        last_configured_subject: None,
        enable_action: "unavailable",
        settings_action: "unavailable",
    }
}
#[cfg(not(target_os = "macos"))]
pub async fn open_settings(_: tauri::AppHandle) -> ActionResult {
    ActionResult {
        outcome: "unavailable",
        message: "Native Vault AutoFill is currently available only in AI Matrx Desktop on macOS."
            .into(),
    }
}
#[cfg(not(target_os = "macos"))]
pub async fn request_enable(_: tauri::AppHandle) -> ActionResult {
    ActionResult {
        outcome: "unavailable",
        message: "Native Vault AutoFill is currently available only in AI Matrx Desktop on macOS."
            .into(),
    }
}
