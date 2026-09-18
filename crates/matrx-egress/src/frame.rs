//! Wire protocol v1 framing — the device leg.
//!
//! Every frame on the device socket is BINARY and laid out exactly as the contract writes it:
//!
//! ```text
//! [u8 type][u32 BE stream_id][payload…]
//! ```
//!
//! Stream 0 is control (HELLO, HELLO_ACK, PING, PONG). There is no length prefix and no
//! continuation bit: the WebSocket message boundary IS the frame boundary, so a frame is exactly
//! one binary message and a message shorter than five bytes is malformed.
//!
//! Contract: `common-docs/systems/platform/residential-egress/FEATURE.md` § "Wire protocol v1 —
//! device leg".

use std::fmt;

/// The control stream. Never carries relayed bytes.
pub const CONTROL_STREAM: u32 = 0;

/// The largest payload a DATA frame may carry (the contract's "≤ 65536 per frame").
pub const MAX_DATA_PAYLOAD: usize = 65536;

/// Frame type bytes, exactly as the contract's table names them.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum FrameType {
    /// `0x01` server→device: open a TCP stream to `{host, port}`.
    Open = 0x01,
    /// `0x02` both ways: raw bytes of the stream.
    Data = 0x02,
    /// `0x03` both ways: the stream is over (half-close is not modelled).
    Close = 0x03,
    /// `0x04` server→device, stream 0.
    Ping = 0x04,
    /// `0x05` device→server, stream 0.
    Pong = 0x05,
    /// `0x10` device→server, stream 0, the first frame on every connection.
    Hello = 0x10,
    /// `0x11` server→device, stream 0.
    HelloAck = 0x11,
    /// `0x12` device→server: the outcome of an OPEN.
    Opened = 0x12,
}

impl FrameType {
    /// The byte this type is written as.
    pub const fn as_u8(self) -> u8 {
        self as u8
    }

    /// Parse a type byte. An unknown byte is `None` — a frame type is never guessed, and the
    /// session refuses the connection rather than acting on a frame it cannot name.
    pub const fn from_u8(byte: u8) -> Option<Self> {
        match byte {
            0x01 => Some(FrameType::Open),
            0x02 => Some(FrameType::Data),
            0x03 => Some(FrameType::Close),
            0x04 => Some(FrameType::Ping),
            0x05 => Some(FrameType::Pong),
            0x10 => Some(FrameType::Hello),
            0x11 => Some(FrameType::HelloAck),
            0x12 => Some(FrameType::Opened),
            _ => None,
        }
    }
}

/// One decoded frame, borrowing nothing: a frame outlives the buffer it arrived in because it is
/// handed to a stream task.
#[derive(Clone, PartialEq, Eq)]
pub struct Frame {
    /// Which of the eight types this is.
    pub kind: FrameType,
    /// 0 for control, otherwise the server's stream id.
    pub stream_id: u32,
    /// Raw payload bytes — JSON for the control types, TCP bytes for DATA.
    pub payload: Vec<u8>,
}

impl fmt::Debug for Frame {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // The payload of a DATA frame is a user's traffic: its LENGTH is loggable, its CONTENT
        // never is. Debug is what ends up in a log line by accident, so it redacts here.
        f.debug_struct("Frame")
            .field("kind", &self.kind)
            .field("stream_id", &self.stream_id)
            .field("payload_len", &self.payload.len())
            .finish()
    }
}

/// Why a byte string is not a frame.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodeError {
    /// Fewer than the five header bytes.
    TooShort {
        /// How many bytes actually arrived.
        len: usize,
    },
    /// The first byte is not one of the eight types.
    UnknownType {
        /// The byte that was read.
        byte: u8,
    },
    /// A DATA frame larger than the contract's per-frame ceiling.
    PayloadTooLarge {
        /// The payload length that arrived.
        len: usize,
    },
}

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DecodeError::TooShort { len } => write!(
                f,
                "a frame needs at least 5 bytes (type + stream id) and this one had {len}"
            ),
            DecodeError::UnknownType { byte } => {
                write!(f, "0x{byte:02x} is not a frame type this protocol version defines")
            }
            DecodeError::PayloadTooLarge { len } => write!(
                f,
                "a data frame may carry at most {MAX_DATA_PAYLOAD} bytes and this one carried {len}"
            ),
        }
    }
}

impl std::error::Error for DecodeError {}

impl Frame {
    /// A control-stream frame carrying JSON.
    pub fn control(kind: FrameType, payload: Vec<u8>) -> Self {
        Frame {
            kind,
            stream_id: CONTROL_STREAM,
            payload,
        }
    }

    /// A frame on one relayed stream.
    pub fn on(kind: FrameType, stream_id: u32, payload: Vec<u8>) -> Self {
        Frame {
            kind,
            stream_id,
            payload,
        }
    }

    /// The bytes to put in one binary WebSocket message.
    pub fn encode(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(5 + self.payload.len());
        out.push(self.kind.as_u8());
        out.extend_from_slice(&self.stream_id.to_be_bytes());
        out.extend_from_slice(&self.payload);
        out
    }

    /// Decode one binary WebSocket message.
    pub fn decode(bytes: &[u8]) -> Result<Self, DecodeError> {
        if bytes.len() < 5 {
            return Err(DecodeError::TooShort { len: bytes.len() });
        }
        let kind =
            FrameType::from_u8(bytes[0]).ok_or(DecodeError::UnknownType { byte: bytes[0] })?;
        let stream_id = u32::from_be_bytes([bytes[1], bytes[2], bytes[3], bytes[4]]);
        let payload = bytes[5..].to_vec();
        if kind == FrameType::Data && payload.len() > MAX_DATA_PAYLOAD {
            return Err(DecodeError::PayloadTooLarge { len: payload.len() });
        }
        Ok(Frame {
            kind,
            stream_id,
            payload,
        })
    }

    /// The payload read as JSON, for the control types.
    pub fn json<T: serde::de::DeserializeOwned>(&self) -> Result<T, serde_json::Error> {
        serde_json::from_slice(&self.payload)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_header_is_one_type_byte_and_four_big_endian_stream_bytes() {
        let frame = Frame::on(FrameType::Data, 0x01020304, b"hi".to_vec());
        assert_eq!(frame.encode(), vec![0x02, 0x01, 0x02, 0x03, 0x04, b'h', b'i']);
    }

    #[test]
    fn every_type_byte_is_the_contracts_byte() {
        for (kind, byte) in [
            (FrameType::Open, 0x01u8),
            (FrameType::Data, 0x02),
            (FrameType::Close, 0x03),
            (FrameType::Ping, 0x04),
            (FrameType::Pong, 0x05),
            (FrameType::Hello, 0x10),
            (FrameType::HelloAck, 0x11),
            (FrameType::Opened, 0x12),
        ] {
            assert_eq!(kind.as_u8(), byte, "{kind:?}");
            assert_eq!(FrameType::from_u8(byte), Some(kind));
        }
    }

    #[test]
    fn a_round_trip_preserves_every_field() {
        for kind in [
            FrameType::Open,
            FrameType::Data,
            FrameType::Close,
            FrameType::Ping,
            FrameType::Pong,
            FrameType::Hello,
            FrameType::HelloAck,
            FrameType::Opened,
        ] {
            let original = Frame::on(kind, 7, b"payload".to_vec());
            let decoded = Frame::decode(&original.encode()).expect("decode");
            assert_eq!(decoded, original, "{kind:?}");
        }
    }

    #[test]
    fn an_empty_payload_is_a_valid_frame() {
        let ping = Frame::control(FrameType::Ping, Vec::new());
        let decoded = Frame::decode(&ping.encode()).expect("decode");
        assert_eq!(decoded.stream_id, CONTROL_STREAM);
        assert!(decoded.payload.is_empty());
    }

    #[test]
    fn a_frame_shorter_than_the_header_is_refused_not_guessed() {
        for len in 0..5usize {
            let bytes = vec![0x02u8; len];
            assert_eq!(Frame::decode(&bytes), Err(DecodeError::TooShort { len }));
        }
    }

    #[test]
    fn an_unknown_type_byte_is_refused() {
        let bytes = [0x7f, 0, 0, 0, 1];
        assert_eq!(
            Frame::decode(&bytes),
            Err(DecodeError::UnknownType { byte: 0x7f })
        );
    }

    #[test]
    fn a_data_frame_over_the_ceiling_is_refused_and_one_exactly_at_it_is_not() {
        let mut at_ceiling = vec![0x02, 0, 0, 0, 9];
        at_ceiling.extend(std::iter::repeat_n(0xabu8, MAX_DATA_PAYLOAD));
        assert!(Frame::decode(&at_ceiling).is_ok());

        let mut over = at_ceiling.clone();
        over.push(0);
        assert_eq!(
            Frame::decode(&over),
            Err(DecodeError::PayloadTooLarge {
                len: MAX_DATA_PAYLOAD + 1
            })
        );
    }

    #[test]
    fn the_stream_id_is_big_endian_across_the_whole_u32_range() {
        for id in [0u32, 1, 255, 256, 65535, 65536, u32::MAX] {
            let encoded = Frame::on(FrameType::Close, id, Vec::new()).encode();
            assert_eq!(
                u32::from_be_bytes([encoded[1], encoded[2], encoded[3], encoded[4]]),
                id
            );
            assert_eq!(Frame::decode(&encoded).expect("decode").stream_id, id);
        }
    }

    #[test]
    fn debug_never_prints_a_users_bytes() {
        let frame = Frame::on(FrameType::Data, 1, b"GET /secret HTTP/1.1".to_vec());
        let rendered = format!("{frame:?}");
        assert!(!rendered.contains("secret"), "{rendered}");
        assert!(rendered.contains("payload_len: 20"), "{rendered}");
    }
}
