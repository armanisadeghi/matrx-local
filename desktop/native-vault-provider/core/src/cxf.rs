//! Strict, closed CXF 1.0 parser for the native file-import boundary.
//! It deliberately accepts only the lossless ES256 passkey subset.
use crate::{SensitiveCose, SourceV1, canonical_source, decode, encode_private, valid_rp};
use base64::{Engine as _, engine::general_purpose::URL_SAFE_NO_PAD};
use coset::{CoseKeyBuilder, iana};
use jiter::{Jiter, NumberInt, Peek};
use p256::{
    SecretKey,
    elliptic_curve::sec1::ToEncodedPoint,
    pkcs8::{DecodePrivateKey, EncodePrivateKey, ObjectIdentifier, PrivateKeyInfo},
};
use std::collections::BTreeSet;
use zeroize::Zeroizing;

const INPUT_LIMIT: usize = 16 * 1024 * 1024;
const MAX_ACCOUNTS: usize = 32;
const MAX_ITEMS: usize = 2_000;
const MAX_CREDENTIALS: usize = 32;
const MAX_TITLE: usize = 1_024;
const MAX_NAME: usize = 256;
const MAX_RP: usize = 253;
const MAX_ID: usize = 1_024;
const MAX_HANDLE: usize = 64;
const MAX_DER: usize = 4_096;
const MAX_DEPTH: usize = 32;
const MAX_GENERIC_SCALAR: usize = MAX_DER * 6;
const MAX_RETAINED: usize = 16 * 1024 * 1024;
const MAX_OBJECT_MEMBERS: usize = 64;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CxfError {
    UnsupportedFormat,
    TransferLimit,
    Cancelled,
}

/// Private candidate held only until the native lifecycle commits it. It has no
/// Debug or Serialize implementation, because the source is credential secret material.
pub struct CxfPasskeyCandidate {
    title: String,
    canonical_source: Zeroizing<Vec<u8>>,
}
impl CxfPasskeyCandidate {
    pub fn title(&self) -> &str {
        &self.title
    }
    pub fn canonical_source(&self) -> &[u8] {
        &self.canonical_source
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CxfUnsupportedReason {
    Scoped,
    MixedOrMultipleCredentials,
    UnsupportedCredential,
}
pub struct CxfUnsupportedItem {
    index: usize,
    title: String,
    reason: CxfUnsupportedReason,
}
impl CxfUnsupportedItem {
    pub fn index(&self) -> usize {
        self.index
    }
    pub fn title(&self) -> &str {
        &self.title
    }
    pub fn reason(&self) -> CxfUnsupportedReason {
        self.reason
    }
}
pub struct CxfInventory {
    candidates: Vec<CxfPasskeyCandidate>,
    unsupported: Vec<CxfUnsupportedItem>,
    total_items: usize,
    unsupported_items: usize,
    retained_bytes: usize,
}
impl CxfInventory {
    pub fn candidates(&self) -> &[CxfPasskeyCandidate] {
        &self.candidates
    }
    pub fn total_items(&self) -> usize {
        self.total_items
    }
    pub fn unsupported_items(&self) -> usize {
        self.unsupported_items
    }
    pub fn unsupported(&self) -> &[CxfUnsupportedItem] {
        &self.unsupported
    }
}

pub fn parse_cxf_v1(bytes: Zeroizing<Vec<u8>>) -> Result<CxfInventory, CxfError> {
    parse_cxf_v1_with_cancel(bytes, || false)
}

/// The owner supplies cancellation from the native operation. Every graph step checks it;
/// dropping the error clears the private input and every retained candidate.
pub fn parse_cxf_v1_with_cancel(
    bytes: Zeroizing<Vec<u8>>,
    mut cancelled: impl FnMut() -> bool,
) -> Result<CxfInventory, CxfError> {
    if bytes.is_empty() || bytes.len() > INPUT_LIMIT {
        return Err(CxfError::TransferLimit);
    }
    let mut p = Jiter::with_bounded_tape_capacity(&bytes, bytes.len().min(MAX_GENERIC_SCALAR));
    let mut inventory = CxfInventory {
        candidates: Vec::new(),
        unsupported: Vec::new(),
        total_items: 0,
        unsupported_items: 0,
        retained_bytes: 0,
    };
    parse_header(&mut p, &mut inventory, 1, &mut cancelled)?;
    p.finish().map_err(|_| CxfError::UnsupportedFormat)?;
    Ok(inventory)
}

fn err<T>() -> Result<T, CxfError> {
    Err(CxfError::UnsupportedFormat)
}
fn key_first(p: &mut Jiter<'_>, depth: usize) -> Result<Option<String>, CxfError> {
    if depth > MAX_DEPTH {
        return Err(CxfError::TransferLimit);
    }
    p.next_bounded_object_key(6 * 64, 64)
        .map_err(|_| CxfError::UnsupportedFormat)
}
fn key_next(p: &mut Jiter<'_>) -> Result<Option<String>, CxfError> {
    p.next_bounded_key(6 * 64, 64)
        .map_err(|_| CxfError::UnsupportedFormat)
}
fn string(p: &mut Jiter<'_>, limit: usize) -> Result<String, CxfError> {
    p.next_bounded_str(limit.checked_mul(6).ok_or(CxfError::TransferLimit)?, limit)
        .map_err(|_| CxfError::UnsupportedFormat)
}
fn object_keys(
    p: &mut Jiter<'_>,
    depth: usize,
    mut f: impl FnMut(&str, &mut Jiter<'_>) -> Result<(), CxfError>,
) -> Result<(), CxfError> {
    let mut seen = BTreeSet::new();
    let mut key = key_first(p, depth)?;
    while let Some(name) = key {
        if seen.len() >= MAX_OBJECT_MEMBERS {
            return Err(CxfError::TransferLimit);
        }
        if !seen.insert(name.clone()) {
            return err();
        }
        f(&name, p)?;
        key = key_next(p)?;
    }
    Ok(())
}
fn empty_array(p: &mut Jiter<'_>) -> Result<(), CxfError> {
    if p.next_array()
        .map_err(|_| CxfError::UnsupportedFormat)?
        .is_some()
    {
        return err();
    }
    Ok(())
}
fn int(p: &mut Jiter<'_>) -> Result<i64, CxfError> {
    match p.next_int().map_err(|_| CxfError::UnsupportedFormat)? {
        NumberInt::Int(v) if v >= 0 => Ok(v),
        _ => err(),
    }
}

fn check_cancel(cancelled: &mut impl FnMut() -> bool) -> Result<(), CxfError> {
    (!cancelled()).then_some(()).ok_or(CxfError::Cancelled)
}
fn encoded_ceiling(bytes: usize) -> Result<usize, CxfError> {
    bytes
        .checked_add(2)
        .and_then(|n| n.checked_div(3))
        .and_then(|n| n.checked_mul(4))
        .ok_or(CxfError::TransferLimit)
}
fn b64_bytes(p: &mut Jiter<'_>, max: usize) -> Result<Zeroizing<Vec<u8>>, CxfError> {
    let encoded = encoded_ceiling(max)?;
    let text = Zeroizing::new(
        p.next_bounded_str(
            encoded.checked_mul(6).ok_or(CxfError::TransferLimit)?,
            encoded,
        )
        .map_err(|_| CxfError::UnsupportedFormat)?,
    );
    let value = Zeroizing::new(decode(&text).map_err(|_| CxfError::UnsupportedFormat)?);
    (!value.is_empty() && value.len() <= max)
        .then_some(value)
        .ok_or(CxfError::UnsupportedFormat)
}
fn b64_id(p: &mut Jiter<'_>) -> Result<(), CxfError> {
    let _ = b64_bytes(p, 64)?;
    Ok(())
}
fn parse_header(
    p: &mut Jiter<'_>,
    inventory: &mut CxfInventory,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut version = false;
    let mut exporter_rp = false;
    let mut exporter_name = false;
    let mut timestamp = false;
    let mut accounts = false;
    object_keys(p, depth, |key, p| match key {
        "version" => {
            if version {
                return err();
            }
            version = true;
            parse_version(p, depth + 1)
        }
        "exporterRpId" => {
            if exporter_rp {
                return err();
            }
            exporter_rp = valid_rp(&string(p, MAX_RP)?);
            exporter_rp.then_some(()).ok_or(CxfError::UnsupportedFormat)
        }
        "exporterDisplayName" => {
            if exporter_name {
                return err();
            }
            exporter_name = true;
            let _ = string(p, MAX_NAME)?;
            Ok(())
        }
        "timestamp" => {
            if timestamp {
                return err();
            }
            timestamp = true;
            let _ = int(p)?;
            Ok(())
        }
        "accounts" => {
            if accounts {
                return err();
            }
            accounts = true;
            parse_accounts(p, inventory, depth + 1, cancelled)
        }
        _ => err(),
    })?;
    (version && exporter_rp && exporter_name && timestamp && accounts)
        .then_some(())
        .ok_or(CxfError::UnsupportedFormat)
}
fn parse_version(p: &mut Jiter<'_>, depth: usize) -> Result<(), CxfError> {
    let mut major = None;
    let mut minor = None;
    object_keys(p, depth, |key, p| match key {
        "major" => {
            if major.is_some() {
                return err();
            }
            major = Some(int(p)?);
            Ok(())
        }
        "minor" => {
            if minor.is_some() {
                return err();
            }
            minor = Some(int(p)?);
            Ok(())
        }
        _ => err(),
    })?;
    (major == Some(1) && minor == Some(0))
        .then_some(())
        .ok_or(CxfError::UnsupportedFormat)
}
fn parse_accounts(
    p: &mut Jiter<'_>,
    inventory: &mut CxfInventory,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut next = p.next_array().map_err(|_| CxfError::UnsupportedFormat)?;
    let mut count = 0;
    while next.is_some() {
        check_cancel(cancelled)?;
        count += 1;
        if count > MAX_ACCOUNTS {
            return Err(CxfError::TransferLimit);
        }
        parse_account(p, inventory, depth + 1, cancelled)?;
        next = p.array_step().map_err(|_| CxfError::UnsupportedFormat)?;
    }
    Ok(())
}
fn parse_account(
    p: &mut Jiter<'_>,
    inventory: &mut CxfInventory,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut id = false;
    let mut username = false;
    let mut email = false;
    let mut collections = false;
    let mut items = false;
    object_keys(p, depth, |key, p| match key {
        "id" => {
            if id {
                return err();
            }
            id = true;
            b64_id(p)
        }
        "username" => {
            if username {
                return err();
            }
            username = true;
            let _ = string(p, MAX_NAME)?;
            Ok(())
        }
        "email" => {
            if email {
                return err();
            }
            email = true;
            let _ = string(p, MAX_NAME)?;
            Ok(())
        }
        "fullName" => {
            let _ = string(p, MAX_NAME)?;
            Ok(())
        }
        "collections" => {
            if collections {
                return err();
            }
            collections = true;
            parse_collections(p, depth + 1, cancelled)
        }
        "items" => {
            if items {
                return err();
            }
            items = true;
            parse_items(p, inventory, depth + 1, cancelled)
        }
        // Account extensions are known but cannot change imported passkeys. The strict lane
        // permits only their absence/empty form; a nonempty extension is not silently lost.
        "extensions" => empty_array(p),
        _ => err(),
    })?;
    (id && username && email && collections && items)
        .then_some(())
        .ok_or(CxfError::UnsupportedFormat)
}
fn parse_collections(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut next = p.next_array().map_err(|_| CxfError::UnsupportedFormat)?;
    while next.is_some() {
        check_cancel(cancelled)?;
        parse_collection(p, depth + 1, cancelled)?;
        next = p.array_step().map_err(|_| CxfError::UnsupportedFormat)?;
    }
    Ok(())
}
fn parse_collection(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut id = false;
    let mut title = false;
    let mut items = false;
    object_keys(p, depth, |key, p| match key {
        "id" => {
            if id {
                return err();
            }
            id = true;
            b64_id(p)
        }
        "title" => {
            if title {
                return err();
            }
            title = true;
            let _ = string(p, MAX_TITLE)?;
            Ok(())
        }
        "creationAt" | "modifiedAt" => {
            let _ = int(p)?;
            Ok(())
        }
        "subtitle" => {
            let _ = string(p, MAX_TITLE)?;
            Ok(())
        }
        "items" => {
            if items {
                return err();
            }
            items = true;
            parse_linked_items(p, depth + 1, cancelled)
        }
        "subCollections" => parse_collections(p, depth + 1, cancelled),
        "extensions" => empty_array(p),
        _ => err(),
    })?;
    (id && title && items)
        .then_some(())
        .ok_or(CxfError::UnsupportedFormat)
}
fn parse_linked_items(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut next = p.next_array().map_err(|_| CxfError::UnsupportedFormat)?;
    while next.is_some() {
        check_cancel(cancelled)?;
        let mut item = false;
        object_keys(p, depth, |key, p| match key {
            "item" => {
                if item {
                    return err();
                }
                item = true;
                b64_id(p)
            }
            "account" => b64_id(p),
            _ => err(),
        })?;
        if !item {
            return err();
        };
        next = p.array_step().map_err(|_| CxfError::UnsupportedFormat)?;
    }
    Ok(())
}
fn parse_items(
    p: &mut Jiter<'_>,
    inventory: &mut CxfInventory,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut next = p.next_array().map_err(|_| CxfError::UnsupportedFormat)?;
    while next.is_some() {
        check_cancel(cancelled)?;
        inventory.total_items += 1;
        if inventory.total_items > MAX_ITEMS {
            return Err(CxfError::TransferLimit);
        }
        parse_item(p, inventory, depth + 1, cancelled)?;
        next = p.array_step().map_err(|_| CxfError::UnsupportedFormat)?;
    }
    Ok(())
}
struct ItemCredentials {
    source: Option<Zeroizing<Vec<u8>>>,
    count: usize,
    unsupported: bool,
}
fn parse_item(
    p: &mut Jiter<'_>,
    inventory: &mut CxfInventory,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut id = false;
    let mut title = None;
    let mut credentials = None;
    let mut scoped = false;
    object_keys(p, depth, |key, p| match key {
        "id" => {
            if id {
                return err();
            }
            id = true;
            b64_id(p)
        }
        "title" => {
            if title.is_some() {
                return err();
            }
            title = Some(string(p, MAX_TITLE)?);
            Ok(())
        }
        "credentials" => {
            if credentials.is_some() {
                return err();
            }
            credentials = Some(parse_credentials(p, depth + 1, cancelled)?);
            Ok(())
        }
        "creationAt" | "modifiedAt" => {
            let _ = int(p)?;
            Ok(())
        }
        "subtitle" => {
            let _ = string(p, MAX_TITLE)?;
            Ok(())
        }
        "favorite" => {
            let _ = p.next_bool().map_err(|_| CxfError::UnsupportedFormat)?;
            Ok(())
        }
        "scope" => {
            scoped = true;
            parse_scope(p, depth + 1, cancelled)
        }
        "tags" => parse_string_array(p, MAX_NAME, depth + 1, cancelled),
        "extensions" => empty_array(p),
        _ => err(),
    })?;
    let title = title.ok_or(CxfError::UnsupportedFormat)?;
    let credentials = credentials.ok_or(CxfError::UnsupportedFormat)?;
    if !id {
        return err();
    }
    // CXF prohibits an Item scope on passkeys. Refuse that whole item after validating
    // the known scope rather than silently retaining a credential with altered use scope.
    let index = inventory.total_items - 1;
    let reason = if scoped {
        Some(CxfUnsupportedReason::Scoped)
    } else if credentials.count > 1 {
        Some(CxfUnsupportedReason::MixedOrMultipleCredentials)
    } else if credentials.unsupported || credentials.source.is_none() {
        Some(CxfUnsupportedReason::UnsupportedCredential)
    } else {
        None
    };
    if let Some(reason) = reason {
        inventory.retained_bytes = inventory
            .retained_bytes
            .checked_add(title.len())
            .filter(|n| *n <= MAX_RETAINED)
            .ok_or(CxfError::TransferLimit)?;
        inventory.unsupported_items += 1;
        inventory.unsupported.push(CxfUnsupportedItem {
            index,
            title,
            reason,
        });
        return Ok(());
    }
    let source = credentials.source.expect("source checked above");
    let next = inventory
        .retained_bytes
        .checked_add(title.len())
        .and_then(|n| n.checked_add(source.len()))
        .ok_or(CxfError::TransferLimit)?;
    if next > MAX_RETAINED || inventory.candidates.len() >= MAX_ITEMS {
        return Err(CxfError::TransferLimit);
    }
    inventory.retained_bytes = next;
    inventory.candidates.push(CxfPasskeyCandidate {
        title,
        canonical_source: source,
    });
    Ok(())
}
fn parse_scope(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut urls = false;
    let mut android = false;
    object_keys(p, depth, |key, p| match key {
        "urls" => {
            if urls {
                return err();
            }
            urls = true;
            parse_string_array(p, MAX_TITLE, depth + 1, cancelled)
        }
        "androidApps" => {
            if android {
                return err();
            }
            android = true;
            parse_android_apps(p, depth + 1, cancelled)
        }
        _ => err(),
    })?;
    (urls && android)
        .then_some(())
        .ok_or(CxfError::UnsupportedFormat)
}
fn parse_string_array(
    p: &mut Jiter<'_>,
    limit: usize,
    _depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut next = p.next_array().map_err(|_| CxfError::UnsupportedFormat)?;
    while next.is_some() {
        check_cancel(cancelled)?;
        let _ = string(p, limit)?;
        next = p.array_step().map_err(|_| CxfError::UnsupportedFormat)?;
    }
    Ok(())
}
fn parse_android_apps(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    let mut next = p.next_array().map_err(|_| CxfError::UnsupportedFormat)?;
    while next.is_some() {
        check_cancel(cancelled)?;
        let mut bundle = false;
        object_keys(p, depth, |key, p| match key {
            "bundleId" => {
                if bundle {
                    return err();
                }
                bundle = true;
                let _ = string(p, MAX_NAME)?;
                Ok(())
            }
            "name" => {
                let _ = string(p, MAX_NAME)?;
                Ok(())
            }
            "certificate" => parse_android_certificate(p, depth + 1),
            _ => err(),
        })?;
        if !bundle {
            return err();
        };
        next = p.array_step().map_err(|_| CxfError::UnsupportedFormat)?;
    }
    Ok(())
}
fn parse_android_certificate(p: &mut Jiter<'_>, depth: usize) -> Result<(), CxfError> {
    let mut fp = false;
    let mut alg = false;
    object_keys(p, depth, |key, p| match key {
        "fingerprint" => {
            if fp {
                return err();
            }
            fp = true;
            b64_id(p)
        }
        "hashAlg" => {
            if alg {
                return err();
            }
            alg = true;
            let _ = string(p, MAX_NAME)?;
            Ok(())
        }
        _ => err(),
    })?;
    (fp && alg).then_some(()).ok_or(CxfError::UnsupportedFormat)
}
fn parse_credentials(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<ItemCredentials, CxfError> {
    let mut next = p.next_array().map_err(|_| CxfError::UnsupportedFormat)?;
    let mut count = 0;
    let mut source = None;
    let mut unsupported = false;
    while next.is_some() {
        check_cancel(cancelled)?;
        count += 1;
        if count > MAX_CREDENTIALS {
            return Err(CxfError::TransferLimit);
        };
        match parse_passkey(p, depth + 1, cancelled)? {
            Some(candidate) if source.is_none() && !unsupported => source = Some(candidate),
            Some(_) | None => unsupported = true,
        };
        next = p.array_step().map_err(|_| CxfError::UnsupportedFormat)?;
    }
    Ok(ItemCredentials {
        source,
        count,
        unsupported,
    })
}
fn parse_passkey(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<Option<Zeroizing<Vec<u8>>>, CxfError> {
    // The type can occur anywhere in a JSON object. Probe with the same bounded walker
    // before consuming the real cursor, so a non-passkey's arbitrary shape is never
    // interpreted as passkey fields merely because its type member came last.
    let type_name =
        p.with_restored_position(|probe| scan_credential_type(probe, depth, cancelled))?;
    if type_name.as_deref() != Some("passkey") {
        discard_value(p, depth, cancelled)?;
        return Ok(None);
    }
    let mut typ = None;
    let mut credential_id = None;
    let mut rp_id = None;
    let mut username = None;
    let mut display = None;
    let mut handle = None;
    let mut der = None;
    let mut unsupported = false;
    object_keys(p, depth, |key, p| match key {
        "type" => {
            if typ.is_some() {
                return err();
            }
            typ = Some(string(p, 32)?);
            Ok(())
        }
        "credentialId" => {
            credential_id = Some(b64_bytes(p, MAX_ID)?);
            Ok(())
        }
        "rpId" => {
            rp_id = Some(string(p, MAX_RP)?);
            Ok(())
        }
        "username" => {
            username = Some(string(p, MAX_NAME)?);
            Ok(())
        }
        "userDisplayName" => {
            display = Some(string(p, MAX_NAME)?);
            Ok(())
        }
        "userHandle" => {
            handle = Some(b64_bytes(p, MAX_HANDLE)?);
            Ok(())
        }
        "key" => {
            der = Some(b64_bytes(p, MAX_DER)?);
            Ok(())
        }
        "fido2Extensions" => {
            unsupported |= !parse_empty_extensions(p, depth + 1, cancelled)?;
            Ok(())
        }
        _ => {
            unsupported = true;
            discard_value(p, depth + 1, cancelled)
        }
    })?;
    if typ.as_deref() != Some("passkey") || unsupported {
        return Ok(None);
    }
    source_from_pkcs8(
        credential_id.ok_or(CxfError::UnsupportedFormat)?,
        &rp_id.ok_or(CxfError::UnsupportedFormat)?,
        username.ok_or(CxfError::UnsupportedFormat)?,
        display.ok_or(CxfError::UnsupportedFormat)?,
        handle.ok_or(CxfError::UnsupportedFormat)?,
        der.ok_or(CxfError::UnsupportedFormat)?,
    )
}
fn scan_credential_type(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<Option<String>, CxfError> {
    let mut typ = None;
    object_keys(p, depth, |key, p| {
        if key == "type" {
            if typ.is_some() {
                return err();
            }
            typ = Some(string(p, 32)?);
            Ok(())
        } else {
            discard_value(p, depth + 1, cancelled)
        }
    })?;
    Ok(typ)
}
fn parse_empty_extensions(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<bool, CxfError> {
    let mut empty = true;
    object_keys(p, depth, |_key, p| {
        empty = false;
        discard_value(p, depth + 1, cancelled)
    })?;
    Ok(empty)
}
/// Consume an unsupported credential/extension using Jiter's grammar, never `next_skip`.
/// Scalars and keys remain bounded, every nested object receives duplicate-key validation.
fn discard_value(
    p: &mut Jiter<'_>,
    depth: usize,
    cancelled: &mut impl FnMut() -> bool,
) -> Result<(), CxfError> {
    check_cancel(cancelled)?;
    if depth > MAX_DEPTH {
        return Err(CxfError::TransferLimit);
    };
    match p.peek().map_err(|_| CxfError::UnsupportedFormat)? {
        Peek::Null => p.next_null().map_err(|_| CxfError::UnsupportedFormat),
        Peek::True | Peek::False => p
            .next_bool()
            .map(|_| ())
            .map_err(|_| CxfError::UnsupportedFormat),
        Peek::String => {
            let _discarded = Zeroizing::new(string(p, MAX_GENERIC_SCALAR)?);
            Ok(())
        }
        Peek::Array => {
            let mut next = p.next_array().map_err(|_| CxfError::UnsupportedFormat)?;
            let mut count = 0;
            while next.is_some() {
                count += 1;
                if count > MAX_ITEMS {
                    return Err(CxfError::TransferLimit);
                };
                discard_value(p, depth + 1, cancelled)?;
                next = p.array_step().map_err(|_| CxfError::UnsupportedFormat)?;
            }
            Ok(())
        }
        Peek::Object => object_keys(p, depth, |_key, p| discard_value(p, depth + 1, cancelled)),
        _ if p.peek().map_err(|_| CxfError::UnsupportedFormat)?.is_num() => {
            let _ = p.next_number().map_err(|_| CxfError::UnsupportedFormat)?;
            Ok(())
        }
        _ => err(),
    }
}
fn source_from_pkcs8(
    credential_id: Zeroizing<Vec<u8>>,
    rp_id: &str,
    username: String,
    display_name: String,
    user_handle: Zeroizing<Vec<u8>>,
    der: Zeroizing<Vec<u8>>,
) -> Result<Option<Zeroizing<Vec<u8>>>, CxfError> {
    if !valid_rp(rp_id) {
        return err();
    }
    if credential_id.is_empty()
        || credential_id.len() > MAX_ID
        || user_handle.is_empty()
        || user_handle.len() > MAX_HANDLE
        || der.is_empty()
        || der.len() > MAX_DER
    {
        return err();
    }
    // Maintained DER decoding validates the complete outer document before
    // algorithm classification. A valid unsupported key is a loss-accounted
    // item, not a reason to discard other supported passkeys in the transfer.
    let info = PrivateKeyInfo::try_from(der.as_slice()).map_err(|_| CxfError::UnsupportedFormat)?;
    const EC_PUBLIC_KEY: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.2.840.10045.2.1");
    const P256_CURVE: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.2.840.10045.3.1.7");
    if info.algorithm.oid != EC_PUBLIC_KEY {
        return Ok(None);
    }
    let curve = info
        .algorithm
        .parameters_oid()
        .map_err(|_| CxfError::UnsupportedFormat)?;
    if curve != P256_CURVE {
        return Ok(None);
    }
    let secret = SecretKey::from_pkcs8_der(&der).map_err(|_| CxfError::UnsupportedFormat)?;
    let canonical_der = secret
        .to_pkcs8_der()
        .map_err(|_| CxfError::UnsupportedFormat)?;
    if canonical_der.as_bytes() != der.as_slice() {
        return err();
    }
    let point = secret.public_key().to_encoded_point(false);
    let private_scalar = Zeroizing::new(secret.to_bytes());
    let cose = SensitiveCose(
        CoseKeyBuilder::new_ec2_priv_key(
            iana::EllipticCurve::P_256,
            point.x().ok_or(CxfError::UnsupportedFormat)?.to_vec(),
            point.y().ok_or(CxfError::UnsupportedFormat)?.to_vec(),
            private_scalar.to_vec(),
        )
        .algorithm(iana::Algorithm::ES256)
        .build(),
    );
    let cose_bytes = cose
        .into_zeroizing_cbor()
        .map_err(|_| CxfError::UnsupportedFormat)?;
    let private_cose_key = Zeroizing::new(URL_SAFE_NO_PAD.encode(&*cose_bytes));
    let value = SourceV1 {
        version: 1,
        credential_id: URL_SAFE_NO_PAD.encode(credential_id),
        rp_id: rp_id.into(),
        user_handle: URL_SAFE_NO_PAD.encode(user_handle),
        username: Some(username),
        display_name: Some(display_name),
        private_cose_key: private_cose_key.into(),
        counter: None,
        extensions: Default::default(),
        backup_eligible: true,
        backup_state: true,
    };
    let bytes = encode_private(|out| serde_json::to_writer(out, &value).map_err(|_| ()))
        .map_err(|_| CxfError::UnsupportedFormat)?;
    canonical_source(&bytes, bytes.len())
        .map(Some)
        .map_err(|_| CxfError::UnsupportedFormat)
}
