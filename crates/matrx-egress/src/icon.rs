//! The tray icon.
//!
//! One 32×32 monochrome PNG, black with an alpha channel — a macOS *template* image, which the
//! system recolours for the light and dark menu bar, and which reads on a Windows tray too. It is
//! a plain house outline: no emoji, no colour, no brand mark that would need signing off.
//!
//! It lives in the crate (`assets/tray-icon.png`) and is compiled in, so a packaged helper has no
//! file to lose.

/// The PNG bytes, as shipped.
pub const TRAY_ICON_PNG: &[u8] = include_bytes!("../assets/tray-icon.png");

/// A decoded icon: RGBA8, row-major, no padding.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Rgba {
    /// Pixels, four bytes each.
    pub bytes: Vec<u8>,
    /// Width in pixels.
    pub width: u32,
    /// Height in pixels.
    pub height: u32,
}

/// Decode the shipped icon.
///
/// Fallible rather than panicking: a tray that cannot draw its icon is a state the caller reports
/// (the helper still runs and the CLI still answers), never a crash on a user's machine.
pub fn tray_icon() -> Result<Rgba, String> {
    decode(TRAY_ICON_PNG)
}

fn decode(bytes: &[u8]) -> Result<Rgba, String> {
    let decoder = png::Decoder::new(std::io::Cursor::new(bytes));
    let mut reader = decoder
        .read_info()
        .map_err(|e| format!("the tray icon could not be read: {e}"))?;
    let mut buffer = vec![0u8; reader.output_buffer_size()];
    let info = reader
        .next_frame(&mut buffer)
        .map_err(|e| format!("the tray icon could not be decoded: {e}"))?;
    if info.color_type != png::ColorType::Rgba || info.bit_depth != png::BitDepth::Eight {
        return Err(format!(
            "the tray icon must be 8-bit RGBA and this one is {:?}/{:?}",
            info.color_type, info.bit_depth
        ));
    }
    buffer.truncate(info.buffer_size());
    Ok(Rgba {
        bytes: buffer,
        width: info.width,
        height: info.height,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_shipped_icon_decodes_to_a_square_rgba_image() {
        let icon = tray_icon().expect("the shipped icon must decode");
        assert_eq!(icon.width, 32);
        assert_eq!(icon.height, 32);
        assert_eq!(icon.bytes.len(), (32 * 32 * 4) as usize);
    }

    #[test]
    fn it_is_monochrome_and_has_transparency_so_the_system_can_tint_it() {
        let icon = tray_icon().expect("icon");
        let mut opaque = 0usize;
        let mut transparent = 0usize;
        for pixel in icon.bytes.chunks_exact(4) {
            let [r, g, b, a] = [pixel[0], pixel[1], pixel[2], pixel[3]];
            assert_eq!((r, g, b), (0, 0, 0), "a coloured pixel in a template image");
            if a == 0 {
                transparent += 1;
            } else {
                opaque += 1;
            }
        }
        assert!(opaque > 100, "the icon is nearly empty ({opaque} pixels)");
        assert!(transparent > 100, "the icon has no transparency");
    }

    #[test]
    fn something_that_is_not_a_png_is_refused_with_a_sentence_rather_than_a_panic() {
        let error = decode(b"not a png at all").expect_err("refused");
        assert!(error.contains("tray icon"), "{error}");
    }
}
