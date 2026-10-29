//! Background login restoration must not request Keychain authorization.
//! Query the same user keychain/service/account as keyring's macOS backend.
//! This does not unlock a keychain, change an ACL, or migrate/delete a credential.

use security_framework::item::{ItemClass, ItemSearchOptions, Limit, SearchResult};
use security_framework::os::macos::keychain::{SecKeychain, SecPreferencesDomain};

pub fn read_without_prompt(service: &str, account: &str) -> Result<Option<String>, String> {
    // Empty attributes can act as wildcards in Keychain Services.
    if service.is_empty() || account.is_empty() {
        return Err("keychain service and account must not be empty".into());
    }
    let keychain = SecKeychain::default_for_domain(SecPreferencesDomain::User)
        .map_err(|e| format!("keychain unavailable ({})", e.code()))?;
    let results = ItemSearchOptions::new()
        .keychains(&[keychain])
        .class(ItemClass::generic_password())
        .service(service)
        .account(account)
        .limit(Limit::Max(1))
        .load_data(true)
        // SecItemCopyMatching skips items that would require a prompt. This is
        // scoped to this query, not a process/system-wide interaction setting.
        .skip_authenticated_items(true)
        .search();
    match results {
        Ok(items) => match items.into_iter().next() {
            Some(SearchResult::Data(bytes)) => String::from_utf8(bytes)
                .map(Some)
                .map_err(|_| "invalid keychain credential encoding".into()),
            None => Ok(None),
            _ => Err("unexpected keychain response".into()),
        },
        // Missing/skipped item or a locked keychain: remain signed out. Never
        // fall back to an interactive read or erase the existing credential.
        Err(e) if unavailable_without_interaction(e.code()) => Ok(None),
        Err(e) => Err(format!("keychain read failed ({})", e.code())),
    }
}

fn unavailable_without_interaction(code: i32) -> bool {
    matches!(code, -25300 | -25308 | -25293) // not found, interaction prohibited, auth failed
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_wildcard_credentials_before_touching_keychain() {
        assert!(read_without_prompt("", "default").is_err());
        assert!(read_without_prompt("enterprise", "").is_err());
    }

    #[test]
    fn only_expected_access_failures_mean_no_silent_session() {
        for code in [-25300, -25308, -25293] {
            assert!(unavailable_without_interaction(code));
        }
        assert!(!unavailable_without_interaction(-50));
        assert!(!unavailable_without_interaction(0));
    }
}
