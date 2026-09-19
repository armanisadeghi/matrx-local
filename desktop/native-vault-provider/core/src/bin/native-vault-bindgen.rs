//! Reproducible UniFFI 0.32.1 Swift/header/modulemap generation for the
//! provider-private static library.  This is a build helper, never an app API.
use std::{env, path::PathBuf};

fn main() {
    let mut args = env::args_os().skip(1);
    let library = args
        .next()
        .map(PathBuf::from)
        .expect("library path required");
    let output = args
        .next()
        .map(PathBuf::from)
        .expect("output directory required");
    if args.next().is_some() {
        panic!("expected library path and output directory");
    }
    uniffi::generate(uniffi::GenerateOptions {
        languages: vec![uniffi::TargetLanguage::Swift],
        source: library.try_into().expect("library path must be UTF-8"),
        out_dir: output.try_into().expect("output directory must be UTF-8"),
        ..Default::default()
    })
    .expect("UniFFI Swift binding generation failed");
}
