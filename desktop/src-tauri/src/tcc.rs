//! macOS privacy (TCC) permissions that THIS process — the visible app — must
//! ask for itself: Contacts, Calendars, Reminders, Photos, Location, Speech
//! Recognition and Bluetooth.
//!
//! Why here and not in the Python engine: macOS shows a privacy prompt for the
//! process that calls the framework request API, attributed to that process's
//! bundle, and only if that bundle's Info.plist carries the matching
//! `NS*UsageDescription` key. The nested engine helper has no run loop
//! (CoreLocation delivers nothing without one) and shipped without the
//! Location/Speech usage keys, so its `requestWhenInUseAuthorization()` was
//! silently ignored — the user pressed "Request access", nothing happened, and
//! System Settings never listed the app (observed 2026-09-13). The app process
//! has the run loop, the keys (`desktop/src-tauri/Info.plist`) and the
//! identity. The engine is a child of this process, so macOS applies the
//! grant to it as well (same code-signing identifier, responsible process).
//!
//! Status reads are the frameworks' class-level `authorizationStatus` queries:
//! read-only, never a prompt. Requests are the frameworks' request APIs and
//! resolve to the real post-prompt status, so the UI never has to guess.
//!
//! Statuses: `granted`, `limited` (granted with a scope the user chose),
//! `denied`, `restricted` (MDM/parental controls), `not_determined` (never
//! asked — the app is not yet listed in System Settings), `unavailable`
//! (not this platform / framework absent).

use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct TccPermissionState {
    pub permission: String,
    pub status: &'static str,
    /// Plain-language fact the UI may show verbatim (never an instruction to
    /// go somewhere the app is not listed).
    pub detail: Option<String>,
    /// Who performs the request for this permission. Always the desktop app
    /// for the keys this module knows; the UI uses it to route clicks.
    pub owner: &'static str,
}

pub const OWNER: &str = "desktop_app";

/// Permission keys this module owns (shared vocabulary with
/// `desktop/src/hooks/use-permissions.ts`).
pub const KEYS: &[&str] = &[
    "contacts",
    "calendar",
    "reminders",
    "photos",
    "location",
    "speech_recognition",
    "bluetooth",
];

fn unavailable(permission: &str, detail: &str) -> TccPermissionState {
    TccPermissionState {
        permission: permission.to_string(),
        status: "unavailable",
        detail: Some(detail.to_string()),
        owner: OWNER,
    }
}

#[cfg(target_os = "macos")]
mod platform {
    use super::{TccPermissionState, OWNER};
    use objc2::rc::Retained;
    use objc2::runtime::{Bool, NSObjectProtocol};
    use objc2::{sel, AnyThread};
    use objc2_contacts::{CNContactStore, CNEntityType};
    use objc2_core_bluetooth::{CBCentralManager, CBManager};
    use objc2_core_location::CLLocationManager;
    use objc2_event_kit::{EKEntityType, EKEventStore};
    use objc2_foundation::NSError;
    use objc2_photos::{PHAccessLevel, PHAuthorizationStatus, PHPhotoLibrary};
    use objc2_speech::{SFSpeechRecognizer, SFSpeechRecognizerAuthorizationStatus};
    use std::cell::RefCell;
    use std::sync::Mutex;
    use std::time::{Duration, Instant};
    use tokio::sync::oneshot;

    /// How long a request may wait for the person to answer the macOS prompt
    /// before the command returns the (still undetermined) status. The prompt
    /// stays on screen; a later status read picks up the answer.
    const PROMPT_WAIT: Duration = Duration::from_secs(120);
    const POLL: Duration = Duration::from_millis(250);

    // Framework objects that must outlive the request they started. The
    // prompt's completion is delivered to the object that asked; dropping it
    // early is how a request "does nothing". Main-thread only.
    thread_local! {
        static CONTACT_STORE: RefCell<Option<Retained<CNContactStore>>> = const { RefCell::new(None) };
        static EVENT_STORE: RefCell<Option<Retained<EKEventStore>>> = const { RefCell::new(None) };
        static LOCATION_MANAGER: RefCell<Option<Retained<CLLocationManager>>> = const { RefCell::new(None) };
        static BLUETOOTH_MANAGER: RefCell<Option<Retained<CBCentralManager>>> = const { RefCell::new(None) };
    }

    fn state(permission: &str, status: &'static str, detail: Option<String>) -> TccPermissionState {
        TccPermissionState {
            permission: permission.to_string(),
            status,
            detail,
            owner: OWNER,
        }
    }

    // ── Read-only status (class methods; never prompt) ───────────────────────

    fn contacts_status() -> &'static str {
        // CNAuthorizationStatus: 0 notDetermined, 1 restricted, 2 denied,
        // 3 authorized, 4 limited (macOS 15+).
        let raw = unsafe { CNContactStore::authorizationStatusForEntityType(CNEntityType::Contacts) }.0;
        match raw {
            0 => "not_determined",
            1 => "restricted",
            2 => "denied",
            3 => "granted",
            4 => "limited",
            _ => "not_determined",
        }
    }

    fn eventkit_status(entity: EKEntityType) -> &'static str {
        // EKAuthorizationStatus: 0 notDetermined, 1 restricted, 2 denied,
        // 3 fullAccess (formerly authorized), 4 writeOnly.
        let raw = unsafe { EKEventStore::authorizationStatusForEntityType(entity) }.0;
        match raw {
            0 => "not_determined",
            1 => "restricted",
            2 => "denied",
            3 => "granted",
            4 => "limited",
            _ => "not_determined",
        }
    }

    fn photos_status() -> &'static str {
        // PHAuthorizationStatus: 0 notDetermined, 1 restricted, 2 denied,
        // 3 authorized, 4 limited.
        let raw = unsafe { PHPhotoLibrary::authorizationStatusForAccessLevel(PHAccessLevel::ReadWrite) }.0;
        match raw {
            0 => "not_determined",
            1 => "restricted",
            2 => "denied",
            3 => "granted",
            4 => "limited",
            _ => "not_determined",
        }
    }

    fn location_status() -> &'static str {
        // CLAuthorizationStatus: 0 notDetermined, 1 restricted, 2 denied,
        // 3 authorizedAlways (the macOS value), 4 authorizedWhenInUse.
        let raw = unsafe { CLLocationManager::authorizationStatus_class() }.0;
        match raw {
            0 => "not_determined",
            1 => "restricted",
            2 => "denied",
            3 | 4 => "granted",
            _ => "not_determined",
        }
    }

    fn location_services_enabled() -> bool {
        unsafe { CLLocationManager::locationServicesEnabled_class() }
    }

    fn speech_status() -> &'static str {
        // SFSpeechRecognizerAuthorizationStatus: 0 notDetermined, 1 denied,
        // 2 restricted, 3 authorized.
        let raw = unsafe { SFSpeechRecognizer::authorizationStatus() }.0;
        match raw {
            0 => "not_determined",
            1 => "denied",
            2 => "restricted",
            3 => "granted",
            _ => "not_determined",
        }
    }

    fn bluetooth_status() -> &'static str {
        // CBManagerAuthorization: 0 notDetermined, 1 restricted, 2 denied,
        // 3 allowedAlways.
        let raw = unsafe { CBManager::authorization_class() }.0;
        match raw {
            0 => "not_determined",
            1 => "restricted",
            2 => "denied",
            3 => "granted",
            _ => "not_determined",
        }
    }

    pub fn status(permission: &str) -> TccPermissionState {
        match permission {
            "contacts" => state(permission, contacts_status(), None),
            "calendar" => state(permission, eventkit_status(EKEntityType::Event), None),
            "reminders" => state(permission, eventkit_status(EKEntityType::Reminder), None),
            "photos" => state(permission, photos_status(), None),
            "speech_recognition" => state(permission, speech_status(), None),
            "bluetooth" => state(permission, bluetooth_status(), None),
            "location" => {
                if !location_services_enabled() {
                    return state(
                        permission,
                        "denied",
                        Some(
                            "Location Services are turned off for this whole Mac. \
                             Turn them on in System Settings → Privacy & Security → \
                             Location Services, then the app can ask for access."
                                .to_string(),
                        ),
                    );
                }
                state(permission, location_status(), None)
            }
            other => super::unavailable(other, "This permission is not owned by the desktop app."),
        }
    }

    // ── Requests (main thread; resolve to the real post-prompt status) ───────

    /// One-shot completion carrier usable from an ObjC block (`Fn`, so the
    /// sender lives behind a mutex and is taken on first call).
    type Completion = Mutex<Option<oneshot::Sender<()>>>;

    fn complete(slot: &Completion) {
        if let Some(tx) = slot.lock().ok().and_then(|mut guard| guard.take()) {
            let _ = tx.send(());
        }
    }

    async fn wait_for(rx: oneshot::Receiver<()>) -> bool {
        tokio::time::timeout(PROMPT_WAIT, rx).await.is_ok()
    }

    /// Poll a class-level status until it leaves `not_determined` or the
    /// prompt wait elapses (CoreLocation and CoreBluetooth have no completion
    /// callback without a delegate).
    async fn wait_until_determined(read: fn() -> &'static str) -> &'static str {
        let started = Instant::now();
        loop {
            let current = read();
            if current != "not_determined" || started.elapsed() >= PROMPT_WAIT {
                return current;
            }
            tokio::time::sleep(POLL).await;
        }
    }

    fn on_main<F: FnOnce() + Send + 'static>(app: &tauri::AppHandle, f: F) -> Result<(), String> {
        app.run_on_main_thread(f)
            .map_err(|error| format!("could not reach the app's main thread: {error}"))
    }

    fn undetermined_detail(answered: bool) -> Option<String> {
        if answered {
            None
        } else {
            Some(
                "macOS has not recorded an answer yet — the permission prompt may still \
                 be open. Answer it, then check again."
                    .to_string(),
            )
        }
    }

    pub async fn request(app: tauri::AppHandle, permission: String) -> Result<TccPermissionState, String> {
        match permission.as_str() {
            "contacts" => {
                let (tx, rx) = oneshot::channel::<()>();
                let slot: Completion = Mutex::new(Some(tx));
                on_main(&app, move || {
                    let store = CONTACT_STORE.with(|cell| {
                        cell.borrow_mut()
                            .get_or_insert_with(|| unsafe { CNContactStore::new() })
                            .clone()
                    });
                    let block = block2::RcBlock::new(move |_granted: Bool, _error: *mut NSError| {
                        complete(&slot);
                    });
                    unsafe {
                        store.requestAccessForEntityType_completionHandler(CNEntityType::Contacts, &block);
                    }
                })?;
                let answered = wait_for(rx).await;
                let mut result = status("contacts");
                if result.status == "not_determined" {
                    result.detail = undetermined_detail(answered);
                }
                Ok(result)
            }
            "calendar" | "reminders" => {
                let (tx, rx) = oneshot::channel::<()>();
                let slot: Completion = Mutex::new(Some(tx));
                let wants_events = permission == "calendar";
                on_main(&app, move || {
                    let store = EVENT_STORE.with(|cell| {
                        cell.borrow_mut()
                            .get_or_insert_with(|| unsafe { EKEventStore::new() })
                            .clone()
                    });
                    let block = block2::RcBlock::new(move |_granted: Bool, _error: *mut NSError| {
                        complete(&slot);
                    });
                    // Handler type is a raw block pointer in EventKit's binding.
                    let handler = &*block as *const _ as *mut block2::DynBlock<dyn Fn(Bool, *mut NSError)>;
                    unsafe {
                        // macOS 14 introduced the full-access requests; on older
                        // systems the selector does not exist and the legacy
                        // entity-type request is the only way to prompt.
                        if wants_events {
                            if store.respondsToSelector(sel!(requestFullAccessToEventsWithCompletion:)) {
                                store.requestFullAccessToEventsWithCompletion(handler);
                            } else {
                                #[allow(deprecated)]
                                store.requestAccessToEntityType_completion(EKEntityType::Event, handler);
                            }
                        } else if store.respondsToSelector(sel!(requestFullAccessToRemindersWithCompletion:)) {
                            store.requestFullAccessToRemindersWithCompletion(handler);
                        } else {
                            #[allow(deprecated)]
                            store.requestAccessToEntityType_completion(EKEntityType::Reminder, handler);
                        }
                    }
                })?;
                let answered = wait_for(rx).await;
                let mut result = status(&permission);
                if result.status == "not_determined" {
                    result.detail = undetermined_detail(answered);
                }
                Ok(result)
            }
            "photos" => {
                let (tx, rx) = oneshot::channel::<()>();
                let slot: Completion = Mutex::new(Some(tx));
                on_main(&app, move || {
                    let block = block2::RcBlock::new(move |_status: PHAuthorizationStatus| {
                        complete(&slot);
                    });
                    unsafe {
                        PHPhotoLibrary::requestAuthorizationForAccessLevel_handler(PHAccessLevel::ReadWrite, &block);
                    }
                })?;
                let answered = wait_for(rx).await;
                let mut result = status("photos");
                if result.status == "not_determined" {
                    result.detail = undetermined_detail(answered);
                }
                Ok(result)
            }
            "speech_recognition" => {
                let (tx, rx) = oneshot::channel::<()>();
                let slot: Completion = Mutex::new(Some(tx));
                on_main(&app, move || {
                    let block = block2::RcBlock::new(move |_status: SFSpeechRecognizerAuthorizationStatus| {
                        complete(&slot);
                    });
                    unsafe {
                        SFSpeechRecognizer::requestAuthorization(&block);
                    }
                })?;
                let answered = wait_for(rx).await;
                let mut result = status("speech_recognition");
                if result.status == "not_determined" {
                    result.detail = undetermined_detail(answered);
                }
                Ok(result)
            }
            "location" => {
                if !location_services_enabled() {
                    // Nothing to ask: macOS will not show a prompt while the
                    // whole service is off. Say so instead of pretending.
                    return Ok(status("location"));
                }
                if location_status() != "not_determined" {
                    // Already answered once; the switch lives in System
                    // Settings now and the app IS listed there.
                    return Ok(status("location"));
                }
                on_main(&app, || {
                    LOCATION_MANAGER.with(|cell| {
                        let manager = cell
                            .borrow_mut()
                            .get_or_insert_with(|| unsafe { CLLocationManager::new() })
                            .clone();
                        unsafe {
                            manager.requestWhenInUseAuthorization();
                            // The TCC check that lists the app under Location
                            // Services fires on the first location use, not on
                            // the authorization request alone.
                            manager.startUpdatingLocation();
                        }
                    });
                })?;
                let final_status = wait_until_determined(location_status).await;
                let _ = on_main(&app, || {
                    LOCATION_MANAGER.with(|cell| {
                        if let Some(manager) = cell.borrow().as_ref() {
                            unsafe { manager.stopUpdatingLocation() };
                        }
                    });
                });
                let mut result = state("location", final_status, None);
                if result.status == "not_determined" {
                    result.detail = undetermined_detail(false);
                }
                Ok(result)
            }
            "bluetooth" => {
                if bluetooth_status() != "not_determined" {
                    return Ok(status("bluetooth"));
                }
                on_main(&app, || {
                    BLUETOOTH_MANAGER.with(|cell| {
                        cell.borrow_mut().get_or_insert_with(|| unsafe {
                            // Creating a central manager is what triggers the
                            // Bluetooth privacy prompt; no delegate is needed
                            // for the prompt itself. Kept alive on purpose.
                            CBCentralManager::initWithDelegate_queue(CBCentralManager::alloc(), None, None)
                        });
                    });
                })?;
                let final_status = wait_until_determined(bluetooth_status).await;
                let mut result = state("bluetooth", final_status, None);
                if result.status == "not_determined" {
                    result.detail = undetermined_detail(false);
                }
                Ok(result)
            }
            other => Ok(super::unavailable(other, "This permission is not owned by the desktop app.")),
        }
    }
}

/// Read-only status of a desktop-app-owned privacy permission. Never prompts.
#[tauri::command]
pub async fn tcc_permission_status(permission: String) -> TccPermissionState {
    if !KEYS.contains(&permission.as_str()) {
        return unavailable(&permission, "This permission is not owned by the desktop app.");
    }
    #[cfg(target_os = "macos")]
    {
        tokio::task::spawn_blocking(move || platform::status(&permission))
            .await
            .unwrap_or_else(|_| unavailable("unknown", "status read did not complete"))
    }
    #[cfg(not(target_os = "macos"))]
    {
        unavailable(&permission, "Privacy permissions are requested by the desktop app on macOS only.")
    }
}

/// Ask macOS for a desktop-app-owned privacy permission and return the real
/// resulting status. Shows the native prompt when the permission has never
/// been asked; otherwise returns the recorded answer without prompting.
#[tauri::command]
pub async fn tcc_request_permission(
    app: tauri::AppHandle,
    permission: String,
) -> Result<TccPermissionState, String> {
    if !KEYS.contains(&permission.as_str()) {
        return Ok(unavailable(&permission, "This permission is not owned by the desktop app."));
    }
    #[cfg(target_os = "macos")]
    {
        platform::request(app, permission).await
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
        Ok(unavailable(&permission, "Privacy permissions are requested by the desktop app on macOS only."))
    }
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    //! Read-only status reads against the real frameworks. These never prompt
    //! (class-level `authorizationStatus` queries) and prove the bindings map
    //! to the vocabulary the desktop hook understands — the failure they
    //! catch is a wrong selector, a wrong enum, or a crash at the FFI edge.
    use super::{platform, KEYS};

    const KNOWN: &[&str] = &[
        "granted",
        "limited",
        "denied",
        "restricted",
        "not_determined",
        "unavailable",
    ];

    #[test]
    fn every_owned_key_reads_a_known_status_without_prompting() {
        for key in KEYS {
            let state = platform::status(key);
            assert_eq!(state.permission, *key);
            assert!(
                KNOWN.contains(&state.status),
                "{key} reported unknown status {}",
                state.status
            );
            assert_eq!(state.owner, super::OWNER);
        }
    }

    #[test]
    fn unknown_keys_are_refused_not_guessed() {
        let state = platform::status("wifi");
        assert_eq!(state.status, "unavailable");
    }
}
