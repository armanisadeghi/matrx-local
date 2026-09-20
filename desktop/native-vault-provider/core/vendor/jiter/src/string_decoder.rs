use std::ops::{Deref, DerefMut, Range};
use std::str::{from_utf8, from_utf8_unchecked};
use zeroize::{Zeroize, Zeroizing};

use crate::errors::{json_err, json_error, JsonErrorType, JsonResult};
use crate::simd::decode_string_chunk;

#[derive(Debug)]
pub struct Tape {
    bytes: Zeroizing<Vec<u8>>,
    maximum: Option<usize>,
}

impl Tape {
    pub fn new() -> Self {
        Self {
            bytes: Zeroizing::new(Vec::new()),
            maximum: None,
        }
    }
    pub fn with_capacity(capacity: usize) -> Self {
        Self {
            bytes: Zeroizing::new(Vec::with_capacity(capacity)),
            maximum: Some(capacity),
        }
    }
    pub fn clear(&mut self) {
        self.bytes.zeroize();
        #[cfg(test)]
        crate::string_decoder::tape_tests::observe_wipe(&self.bytes);
        self.bytes.clear();
    }
    pub fn empty_clone(&self) -> Self {
        match self.maximum {
            Some(capacity) => Self::with_capacity(capacity),
            None => Self::new(),
        }
    }
    pub fn capacity_limit(&self) -> Option<usize> {
        self.maximum
    }
    fn permits(&self, added: usize) -> bool {
        self.maximum.is_none_or(|max| {
            self.bytes
                .len()
                .checked_add(added)
                .is_some_and(|next| next <= max)
        })
    }
    pub fn try_extend_from_slice(&mut self, bytes: &[u8]) -> bool {
        if !self.permits(bytes.len()) {
            return false;
        }
        self.bytes.extend_from_slice(bytes);
        true
    }
    pub fn try_push(&mut self, byte: u8) -> bool {
        if !self.permits(1) {
            return false;
        }
        self.bytes.push(byte);
        true
    }
}
impl Drop for Tape {
    fn drop(&mut self) {
        self.clear();
    }
}
impl Default for Tape {
    fn default() -> Self {
        Self::new()
    }
}
impl Deref for Tape {
    type Target = Vec<u8>;
    fn deref(&self) -> &Self::Target {
        &self.bytes
    }
}
impl DerefMut for Tape {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.bytes
    }
}

/// `'t` is the lifetime of the tape (reusable buffer), `'j` is the lifetime of the JSON data itself
/// data must outlive tape, so if you return data with the lifetime of tape,
/// a slice of data the original JSON data is okay too
pub trait AbstractStringDecoder<'t, 'j>
where
    'j: 't,
{
    type Output: std::fmt::Debug;

    fn decode(
        data: &'j [u8],
        index: usize,
        tape: &'t mut Tape,
        allow_partial: bool,
    ) -> JsonResult<(Self::Output, usize)>;
}

pub struct StringDecoder;

#[derive(Debug)]
pub enum StringOutputType<'t, 'j>
where
    'j: 't,
{
    Tape(&'t str),
    Data(&'j str),
}

/// This submodule is used to create a safety boundary where the `ascii_only`
/// flag can be used to carry soundness information about the string.
mod string_output {
    use std::borrow::Cow;

    use super::StringOutputType;

    #[derive(Debug)]
    pub struct StringOutput<'t, 'j>
    where
        'j: 't,
    {
        pub(crate) data: StringOutputType<'t, 'j>,
        // SAFETY: this is used as an invariant to determine if the string is ascii only
        // so this should not be set except when known
        ascii_only: bool,
    }

    impl From<StringOutput<'_, '_>> for String {
        fn from(val: StringOutput) -> Self {
            match val.data {
                StringOutputType::Tape(s) | StringOutputType::Data(s) => s.to_owned(),
            }
        }
    }

    impl<'j> From<StringOutput<'_, 'j>> for Cow<'j, str> {
        fn from(val: StringOutput<'_, 'j>) -> Self {
            match val.data {
                StringOutputType::Tape(s) => s.to_owned().into(),
                StringOutputType::Data(s) => s.into(),
            }
        }
    }

    impl<'t, 'j> StringOutput<'t, 'j>
    where
        'j: 't,
    {
        /// # Safety
        ///
        /// `ascii_only` must only be set to true if the string is ASCII only
        pub unsafe fn tape(data: &'t str, ascii_only: bool) -> Self {
            StringOutput {
                data: StringOutputType::Tape(data),
                ascii_only,
            }
        }

        /// # Safety
        ///
        /// `ascii_only` must only be set to true if the string is ASCII only
        pub unsafe fn data(data: &'j str, ascii_only: bool) -> Self {
            StringOutput {
                data: StringOutputType::Data(data),
                ascii_only,
            }
        }

        pub fn as_str(&self) -> &'t str {
            match self.data {
                StringOutputType::Tape(s) | StringOutputType::Data(s) => s,
            }
        }

        pub fn ascii_only(&self) -> bool {
            self.ascii_only
        }
    }
}

pub use string_output::StringOutput;

impl<'t, 'j> AbstractStringDecoder<'t, 'j> for StringDecoder
where
    'j: 't,
{
    type Output = StringOutput<'t, 'j>;

    fn decode(
        data: &'j [u8],
        index: usize,
        tape: &'t mut Tape,
        allow_partial: bool,
    ) -> JsonResult<(Self::Output, usize)> {
        let start = index + 1;

        match decode_string_chunk(data, start, true, allow_partial)? {
            (StringChunk::StringEnd, ascii_only, index) => {
                let s = to_str(&data[start..index], ascii_only, start, allow_partial)?;
                // SAFETY: `ascii_only` tracks whether the decoded string contains only ASCII.
                Ok((unsafe { StringOutput::data(s, ascii_only) }, index + 1))
            }
            (StringChunk::Backslash, ascii_only, index) => {
                decode_to_tape(data, index, tape, start, ascii_only, allow_partial)
            }
        }
    }
}

fn decode_to_tape<'t, 'j>(
    data: &'j [u8],
    mut index: usize,
    tape: &'t mut Tape,
    start: usize,
    mut ascii_only: bool,
    allow_partial: bool,
) -> JsonResult<(StringOutput<'t, 'j>, usize)> {
    tape.clear();
    let mut chunk_start = start;
    loop {
        // on_backslash
        if !tape.try_extend_from_slice(&data[chunk_start..index]) {
            return json_err!(ExpectedSomeValue, index);
        }
        index += 1;
        if let Some(next_inner) = data.get(index) {
            match next_inner {
                b'"' | b'\\' | b'/' => {
                    if !tape.try_push(*next_inner) {
                        return json_err!(ExpectedSomeValue, index);
                    }
                }
                b'b' => {
                    if !tape.try_push(b'\x08') {
                        return json_err!(ExpectedSomeValue, index);
                    }
                }
                b'f' => {
                    if !tape.try_push(b'\x0C') {
                        return json_err!(ExpectedSomeValue, index);
                    }
                }
                b'n' => {
                    if !tape.try_push(b'\n') {
                        return json_err!(ExpectedSomeValue, index);
                    }
                }
                b'r' => {
                    if !tape.try_push(b'\r') {
                        return json_err!(ExpectedSomeValue, index);
                    }
                }
                b't' => {
                    if !tape.try_push(b'\t') {
                        return json_err!(ExpectedSomeValue, index);
                    }
                }
                b'u' => match parse_escape(data, index) {
                    Ok((c, new_index)) => {
                        ascii_only = false;
                        index = new_index;
                        if !tape.try_extend_from_slice(c.encode_utf8(&mut [0_u8; 4]).as_bytes()) {
                            return json_err!(ExpectedSomeValue, index);
                        }
                    }
                    Err(e) => {
                        if allow_partial && e.error_type == JsonErrorType::EofWhileParsingString {
                            let s = to_str(tape, ascii_only, start, allow_partial)?;
                            // SAFETY: `ascii_only` tracks whether the decoded string contains only ASCII.
                            return Ok((unsafe { StringOutput::tape(s, ascii_only) }, e.index));
                        }
                        return Err(e);
                    }
                },
                _ => return json_err!(InvalidEscape, index),
            }
            index += 1;
        } else {
            if allow_partial {
                let s = to_str(tape, ascii_only, start, allow_partial)?;
                // SAFETY: `ascii_only` tracks whether the decoded string contains only ASCII.
                return Ok((unsafe { StringOutput::tape(s, ascii_only) }, index));
            }
            return json_err!(EofWhileParsingString, index);
        }

        match decode_string_chunk(data, index, ascii_only, allow_partial)? {
            (StringChunk::StringEnd, ascii_only, new_index) => {
                if !tape.try_extend_from_slice(&data[index..new_index]) {
                    return json_err!(ExpectedSomeValue, index);
                }
                index = new_index + 1;
                let s = to_str(tape, ascii_only, start, allow_partial)?;
                // SAFETY: `ascii_only` tracks whether the decoded string contains only ASCII.
                return Ok((unsafe { StringOutput::tape(s, ascii_only) }, index));
            }
            (StringChunk::Backslash, ascii_only_new, index_new) => {
                ascii_only = ascii_only_new;
                chunk_start = index;
                index = index_new;
            }
        }
    }
}

pub(crate) enum StringChunk {
    StringEnd,
    Backslash,
}

fn to_str(bytes: &[u8], ascii_only: bool, start: usize, allow_partial: bool) -> JsonResult<&str> {
    if ascii_only {
        // safety: in this case we've already confirmed that all characters are ascii, we can safely
        // transmute from bytes to str
        Ok(unsafe { from_utf8_unchecked(bytes) })
    } else {
        match from_utf8(bytes) {
            Ok(s) => Ok(s),
            Err(e) if allow_partial && e.error_len().is_none() => {
                // In partial mode, we handle incomplete (not invalid) UTF-8 sequences
                // by truncating to the last valid UTF-8 boundary
                // (`error_len()` is `None` for incomplete sequences)
                let valid_up_to = e.valid_up_to();
                // SAFETY: `valid_up_to()` returns the byte index up to which the input is valid UTF-8
                Ok(unsafe { from_utf8_unchecked(&bytes[..valid_up_to]) })
            }
            Err(e) => Err(json_error!(
                InvalidUnicodeCodePoint,
                start + e.valid_up_to() + 1
            )),
        }
    }
}

/// Taken approximately from https://github.com/serde-rs/json/blob/v1.0.107/src/read.rs#L872-L945
fn parse_escape(data: &[u8], index: usize) -> JsonResult<(char, usize)> {
    let (n, index) = parse_u4(data, index)?;
    match n {
        0xDC00..=0xDFFF => json_err!(LoneLeadingSurrogateInHexEscape, index),
        0xD800..=0xDBFF => match data.get(index + 1..index + 3) {
            Some(slice) if slice == b"\\u" => {
                let (n2, index) = parse_u4(data, index + 2)?;
                if !(0xDC00..=0xDFFF).contains(&n2) {
                    return json_err!(LoneLeadingSurrogateInHexEscape, index);
                }
                let n2 = ((((n - 0xD800) as u32) << 10) | ((n2 - 0xDC00) as u32)) + 0x1_0000;

                match char::from_u32(n2) {
                    Some(c) => Ok((c, index)),
                    None => json_err!(EofWhileParsingString, index),
                }
            }
            Some(slice) if slice.starts_with(b"\\") => {
                json_err!(UnexpectedEndOfHexEscape, index + 2)
            }
            Some(_) => json_err!(UnexpectedEndOfHexEscape, index + 1),
            None => match data.get(index + 1) {
                Some(b'\\') | None => json_err!(EofWhileParsingString, data.len()),
                Some(_) => json_err!(UnexpectedEndOfHexEscape, index + 1),
            },
        },
        _ => match char::from_u32(n as u32) {
            Some(c) => Ok((c, index)),
            None => json_err!(InvalidEscape, index),
        },
    }
}

fn parse_u4(data: &[u8], mut index: usize) -> JsonResult<(u16, usize)> {
    let mut n = 0;
    let u4 = data
        .get(index + 1..index + 5)
        .ok_or_else(|| json_error!(EofWhileParsingString, data.len()))?;

    for c in u4 {
        index += 1;
        let hex = match c {
            b'0'..=b'9' => (c & 0x0f) as u16,
            b'a'..=b'f' => (c - b'a' + 10) as u16,
            b'A'..=b'F' => (c - b'A' + 10) as u16,
            _ => return json_err!(InvalidEscape, index),
        };
        n = (n << 4) + hex;
    }
    Ok((n, index))
}

/// A string decoder that returns the range of the string.
///
/// *WARNING:* For performance reasons, this decoder does not check that the string would be valid UTF-8.
pub struct StringDecoderRange;

impl<'t, 'j> AbstractStringDecoder<'t, 'j> for StringDecoderRange
where
    'j: 't,
{
    type Output = Range<usize>;

    fn decode(
        data: &'j [u8],
        mut index: usize,
        _tape: &'t mut Tape,
        allow_partial: bool,
    ) -> JsonResult<(Self::Output, usize)> {
        index += 1;
        let start = index;

        loop {
            index = match decode_string_chunk(data, index, true, allow_partial)? {
                (StringChunk::StringEnd, _, index) => {
                    let r = start..index;
                    return Ok((r, index + 1));
                }
                (StringChunk::Backslash, _, index) => index,
            };
            index += 1;
            if let Some(next_inner) = data.get(index) {
                match next_inner {
                    // these escapes are easy to validate
                    b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't' => (),
                    b'u' => {
                        let (_, new_index) = parse_escape(data, index)?;
                        index = new_index;
                    }
                    _ => return json_err!(InvalidEscape, index),
                }
                index += 1;
            } else {
                return json_err!(EofWhileParsingString, index);
            }
        }
    }
}

#[cfg(test)]
mod tape_tests {
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    static OBSERVED_CLEAR: AtomicUsize = AtomicUsize::new(0);
    static OBSERVED_WIPE: AtomicBool = AtomicBool::new(false);

    pub(super) fn observe_wipe(bytes: &[u8]) {
        OBSERVED_CLEAR.fetch_add(1, Ordering::SeqCst);
        OBSERVED_WIPE.store(bytes.iter().all(|byte| *byte == 0), Ordering::SeqCst);
    }

    #[test]
    fn fixed_tape_refuses_tail_after_escape_that_would_grow_allocation() {
        // The final plain tail follows an escaped segment; it used to bypass try_extend.
        let mut tape = Tape::with_capacity(2);
        let input = br#""a\u0061zz""#;
        assert!(StringDecoder::decode(input, 0, &mut tape, false).is_err());
        assert_eq!(tape.bytes.capacity(), 2);
    }

    #[test]
    fn escaped_decode_clear_and_drop_wipe_the_same_fixed_allocation() {
        OBSERVED_CLEAR.store(0, Ordering::SeqCst);
        OBSERVED_WIPE.store(false, Ordering::SeqCst);
        let mut tape = Tape::with_capacity(64);
        let address = tape.bytes.as_ptr();
        let capacity = tape.bytes.capacity();
        let input = br#""private\u002dmaterial""#;
        {
            let (decoded, _) = StringDecoder::decode(input, 0, &mut tape, false).unwrap();
            assert_eq!(decoded.as_str(), "private-material");
        }
        tape.clear();
        assert_eq!(tape.bytes.as_ptr(), address);
        assert_eq!(tape.bytes.capacity(), capacity);
        assert!(OBSERVED_WIPE.load(Ordering::SeqCst));
        assert!(OBSERVED_CLEAR.load(Ordering::SeqCst) >= 1);
        drop(tape);
        assert!(OBSERVED_CLEAR.load(Ordering::SeqCst) >= 2);
    }
}
