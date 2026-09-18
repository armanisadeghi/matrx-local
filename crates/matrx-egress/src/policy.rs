//! The public-address policy — contract rule 3.
//!
//! > "The helper connects only to PUBLIC internet addresses (it resolves the name, then refuses
//! > loopback / RFC1918 / link-local / multicast / reserved) — nobody can use it to reach the
//! > user's LAN."
//!
//! Two decisions this module makes, both deliberately strict:
//!
//! 1. **The check is on the RESOLVED addresses, never on the name.** A name is not evidence:
//!    `home.example.com` can be an `A` record for `192.168.1.1`.
//! 2. **ONE non-public answer refuses the whole OPEN.** A name that resolves to a public address
//!    *and* a private one is a rebinding shape, and picking the public one would let the next
//!    lookup land inside the LAN. The stream is refused with `policy`.
//!
//! The connect then uses exactly the addresses this module approved — it never re-resolves, so
//! there is no window between the check and the connection.

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};

/// Why an address may not be dialled. Every variant is a plain phrase that goes into the OPENED
/// frame's `message`, so the server (and a support conversation) can say what happened.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal {
    /// `0.0.0.0`, `::`, or a port of 0.
    Unspecified,
    /// `127.0.0.0/8`, `::1` — this computer itself.
    Loopback,
    /// RFC1918, unique-local, and the carrier-grade NAT range: the user's own network.
    PrivateNetwork,
    /// `169.254.0.0/16`, `fe80::/10`.
    LinkLocal,
    /// `224.0.0.0/4`, `ff00::/8`, and the IPv4 broadcast address.
    Multicast,
    /// Documentation, benchmarking, protocol-assignment and future-use ranges.
    Reserved,
}

impl Refusal {
    /// The sentence the OPENED frame carries.
    pub const fn message(self) -> &'static str {
        match self {
            Refusal::Unspecified => {
                "that address is not a reachable internet address, so this computer refused it"
            }
            Refusal::Loopback => {
                "that address points back at this computer, and the home connection only carries \
                 traffic to the public internet"
            }
            Refusal::PrivateNetwork => {
                "that address is on a private network, and the home connection only carries \
                 traffic to the public internet"
            }
            Refusal::LinkLocal => {
                "that address is a local-network-only address, and the home connection only \
                 carries traffic to the public internet"
            }
            Refusal::Multicast => {
                "that address is a multicast or broadcast address, which the home connection \
                 never carries"
            }
            Refusal::Reserved => {
                "that address is in a reserved range that is not routable on the public internet"
            }
        }
    }
}

/// Judge one resolved address. `Ok(())` means public.
pub fn judge(addr: IpAddr) -> Result<(), Refusal> {
    match addr {
        IpAddr::V4(v4) => judge_v4(v4),
        IpAddr::V6(v6) => judge_v6(v6),
    }
}

fn judge_v4(ip: Ipv4Addr) -> Result<(), Refusal> {
    let [a, b, c, _d] = ip.octets();
    if ip.is_unspecified() {
        return Err(Refusal::Unspecified);
    }
    if ip.is_loopback() {
        return Err(Refusal::Loopback);
    }
    if ip.is_broadcast() || ip.is_multicast() {
        return Err(Refusal::Multicast);
    }
    if ip.is_link_local() {
        return Err(Refusal::LinkLocal);
    }
    if ip.is_private() {
        return Err(Refusal::PrivateNetwork);
    }
    // 100.64.0.0/10 — carrier-grade NAT (RFC 6598). `std` has no predicate for it, and it is the
    // range a user's router hands out behind a mobile or fibre CGNAT, so it is LAN for our
    // purposes.
    if a == 100 && (64..=127).contains(&b) {
        return Err(Refusal::PrivateNetwork);
    }
    // 0.0.0.0/8 "this network" — only 0.0.0.0 itself is `is_unspecified`.
    if a == 0 {
        return Err(Refusal::Reserved);
    }
    // 192.0.0.0/24 IETF protocol assignments, 192.0.2.0/24 TEST-NET-1,
    // 198.51.100.0/24 TEST-NET-2, 203.0.113.0/24 TEST-NET-3, 198.18.0.0/15 benchmarking.
    if (a == 192 && b == 0 && (c == 0 || c == 2))
        || (a == 198 && b == 51 && c == 100)
        || (a == 203 && b == 0 && c == 113)
        || (a == 198 && (b == 18 || b == 19))
    {
        return Err(Refusal::Reserved);
    }
    // 240.0.0.0/4 reserved for future use (255.255.255.255 was already caught as broadcast).
    if a >= 240 {
        return Err(Refusal::Reserved);
    }
    Ok(())
}

fn judge_v6(ip: Ipv6Addr) -> Result<(), Refusal> {
    // An IPv4 address wearing an IPv6 coat is still that IPv4 address. Both the mapped form
    // (::ffff:a.b.c.d) and NAT64's well-known prefix (64:ff9b::/96) are judged as the v4 inside,
    // or ::ffff:192.168.1.1 would walk straight through an IPv6-only predicate set.
    if let Some(v4) = ip.to_ipv4_mapped() {
        return judge_v4(v4);
    }
    let segments = ip.segments();
    if segments[0] == 0x0064 && segments[1] == 0xff9b && segments[2..6] == [0, 0, 0, 0] {
        let v4 = Ipv4Addr::new(
            (segments[6] >> 8) as u8,
            (segments[6] & 0xff) as u8,
            (segments[7] >> 8) as u8,
            (segments[7] & 0xff) as u8,
        );
        return judge_v4(v4);
    }
    // These two come BEFORE the IPv4-compatible conversion below, because `::1.to_ipv4()` is
    // `Some(0.0.0.1)` — the loopback address would otherwise be refused as "reserved", which is
    // true but says the wrong thing to whoever reads the message.
    if ip.is_unspecified() {
        return Err(Refusal::Unspecified);
    }
    if ip.is_loopback() {
        return Err(Refusal::Loopback);
    }
    // The deprecated IPv4-compatible form (::a.b.c.d), which `to_ipv4_mapped` does not cover.
    if let Some(v4) = ip.to_ipv4() {
        return judge_v4(v4);
    }
    if ip.is_multicast() {
        return Err(Refusal::Multicast);
    }
    // fe80::/10 link-local unicast.
    if segments[0] & 0xffc0 == 0xfe80 {
        return Err(Refusal::LinkLocal);
    }
    // fc00::/7 unique local — the IPv6 LAN.
    if segments[0] & 0xfe00 == 0xfc00 {
        return Err(Refusal::PrivateNetwork);
    }
    // 100::/64 discard-only, 2001:db8::/32 documentation, 2001::/23 IETF protocol assignments
    // (Teredo 2001::/32 among them), 2002::/16 6to4 relays.
    if segments[0] == 0x0100 && segments[1..4] == [0, 0, 0] {
        return Err(Refusal::Reserved);
    }
    if segments[0] == 0x2001 && segments[1] == 0x0db8 {
        return Err(Refusal::Reserved);
    }
    if segments[0] == 0x2001 && segments[1] < 0x0200 {
        return Err(Refusal::Reserved);
    }
    if segments[0] == 0x2002 {
        return Err(Refusal::Reserved);
    }
    Ok(())
}

/// Judge a whole resolution. Returns the first refusal found, so ONE private answer refuses the
/// OPEN even when a public answer sits beside it.
pub fn judge_all(addrs: &[IpAddr]) -> Result<(), Refusal> {
    if addrs.is_empty() {
        // The caller turns an empty resolution into `dns`, never `policy`; this is belt and
        // braces so an empty list can never be read as "nothing objectionable, go ahead".
        return Err(Refusal::Unspecified);
    }
    for addr in addrs {
        judge(*addr)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::str::FromStr;

    /// The table the contract's rule 3 describes, address by address.
    #[test]
    fn the_address_table() {
        let cases: &[(&str, Option<Refusal>)] = &[
            // Public — the whole point of the helper.
            ("1.1.1.1", None),
            ("8.8.8.8", None),
            ("104.26.12.205", None),   // api.ipify.org, as of this build
            ("172.15.255.255", None),  // just below the RFC1918 block
            ("172.32.0.1", None),      // just above it
            ("100.63.255.255", None),  // just below CGNAT
            ("100.128.0.1", None),     // just above CGNAT
            ("239.255.255.255", Some(Refusal::Multicast)),
            ("2606:4700:4700::1111", None),
            ("2a00:1450:4001:80e::200e", None),
            // This computer.
            ("0.0.0.0", Some(Refusal::Unspecified)),
            ("127.0.0.1", Some(Refusal::Loopback)),
            ("127.255.255.254", Some(Refusal::Loopback)),
            ("::", Some(Refusal::Unspecified)),
            ("::1", Some(Refusal::Loopback)),
            // The user's LAN.
            ("10.0.0.1", Some(Refusal::PrivateNetwork)),
            ("10.255.255.255", Some(Refusal::PrivateNetwork)),
            ("172.16.0.1", Some(Refusal::PrivateNetwork)),
            ("172.31.255.255", Some(Refusal::PrivateNetwork)),
            ("192.168.1.1", Some(Refusal::PrivateNetwork)),
            ("100.64.0.1", Some(Refusal::PrivateNetwork)),
            ("100.127.255.255", Some(Refusal::PrivateNetwork)),
            ("fd00::1", Some(Refusal::PrivateNetwork)),
            ("fc00::1", Some(Refusal::PrivateNetwork)),
            // Link-local, including the cloud metadata address every SSRF story ends at.
            ("169.254.0.1", Some(Refusal::LinkLocal)),
            ("169.254.169.254", Some(Refusal::LinkLocal)),
            ("fe80::1", Some(Refusal::LinkLocal)),
            ("febf::1", Some(Refusal::LinkLocal)),
            // Multicast and broadcast.
            ("224.0.0.1", Some(Refusal::Multicast)),
            ("255.255.255.255", Some(Refusal::Multicast)),
            ("ff02::1", Some(Refusal::Multicast)),
            // Reserved.
            ("0.1.2.3", Some(Refusal::Reserved)),
            ("192.0.0.1", Some(Refusal::Reserved)),
            ("192.0.2.1", Some(Refusal::Reserved)),
            ("198.18.0.1", Some(Refusal::Reserved)),
            ("198.19.255.255", Some(Refusal::Reserved)),
            ("198.51.100.1", Some(Refusal::Reserved)),
            ("203.0.113.1", Some(Refusal::Reserved)),
            ("240.0.0.1", Some(Refusal::Reserved)),
            ("100::1", Some(Refusal::Reserved)),
            ("2001:db8::1", Some(Refusal::Reserved)),
            ("2001::1", Some(Refusal::Reserved)),        // Teredo
            ("2002:c0a8:0101::1", Some(Refusal::Reserved)), // 6to4
            // The IPv6 disguises.
            ("::ffff:192.168.1.1", Some(Refusal::PrivateNetwork)),
            ("::ffff:127.0.0.1", Some(Refusal::Loopback)),
            ("::ffff:169.254.169.254", Some(Refusal::LinkLocal)),
            ("::ffff:8.8.8.8", None),
            ("64:ff9b::192.168.1.1", Some(Refusal::PrivateNetwork)),
            ("64:ff9b::8.8.8.8", None),
            ("::127.0.0.1", Some(Refusal::Loopback)),
        ];

        for (text, expected) in cases {
            let addr = IpAddr::from_str(text).unwrap_or_else(|e| panic!("{text}: {e}"));
            let actual = judge(addr).err();
            assert_eq!(actual, *expected, "{text} judged {actual:?}");
        }
    }

    #[test]
    fn one_private_answer_refuses_a_resolution_that_also_has_a_public_one() {
        let addrs = vec![
            IpAddr::from_str("93.184.216.34").expect("public"),
            IpAddr::from_str("192.168.0.5").expect("private"),
        ];
        assert_eq!(judge_all(&addrs), Err(Refusal::PrivateNetwork));
        // …and the order does not matter.
        let reversed: Vec<_> = addrs.into_iter().rev().collect();
        assert_eq!(judge_all(&reversed), Err(Refusal::PrivateNetwork));
    }

    #[test]
    fn an_all_public_resolution_passes() {
        let addrs = vec![
            IpAddr::from_str("1.1.1.1").expect("v4"),
            IpAddr::from_str("2606:4700:4700::1111").expect("v6"),
        ];
        assert_eq!(judge_all(&addrs), Ok(()));
    }

    #[test]
    fn an_empty_resolution_is_never_read_as_permission() {
        assert!(judge_all(&[]).is_err());
    }

    #[test]
    fn every_refusal_has_a_plain_sentence() {
        for refusal in [
            Refusal::Unspecified,
            Refusal::Loopback,
            Refusal::PrivateNetwork,
            Refusal::LinkLocal,
            Refusal::Multicast,
            Refusal::Reserved,
        ] {
            let message = refusal.message();
            assert!(message.len() > 20, "{refusal:?}: {message}");
            for jargon in ["RFC", "CIDR", "egress", "proxy", "/8", "/16"] {
                assert!(!message.contains(jargon), "{refusal:?} says {jargon:?}");
            }
        }
    }
}
