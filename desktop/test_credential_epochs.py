"""Account switches must invalidate only the matching source's validation."""
import credential_epochs as epochs


def test_source_epochs_rotate_independently(monkeypatch):
    values = {}
    monkeypatch.setattr(epochs.keyring, 'get_password', lambda service, name: values.get(name))
    monkeypatch.setattr(epochs.keyring, 'set_password', lambda service, name, value: values.__setitem__(name, value))
    monkeypatch.setattr(epochs.secrets, 'token_urlsafe', lambda size: f'random-{len(values)}-{size}')
    first = {'workbuddy': epochs.current('workbuddy', 'wb-account-a'),
             'raccoon': epochs.current('raccoon', 'raccoon-account-a')}
    assert epochs.current('workbuddy', 'wb-account-a') == first['workbuddy']
    assert epochs.current('raccoon', 'raccoon-account-a') == first['raccoon']
    raccoon_next = epochs.current('raccoon', 'raccoon-account-b')
    assert raccoon_next != first['raccoon']
    assert epochs.current('workbuddy', 'wb-account-a') == first['workbuddy']
    workbuddy_next = epochs.current('workbuddy', 'wb-account-b')
    assert workbuddy_next != first['workbuddy']
    assert epochs.current('raccoon', 'raccoon-account-b') == raccoon_next
    assert all('account' not in value for value in values.values())


def test_manual_rotation_changes_only_selected_source(monkeypatch):
    values = {}
    monkeypatch.setattr(epochs.keyring, 'get_password', lambda service, name: values.get(name))
    monkeypatch.setattr(epochs.keyring, 'set_password', lambda service, name, value: values.__setitem__(name, value))
    old_workbuddy = epochs.current('workbuddy')
    old_raccoon = epochs.current('raccoon')
    assert epochs.rotate('raccoon') != old_raccoon
    assert epochs.current('workbuddy') == old_workbuddy
