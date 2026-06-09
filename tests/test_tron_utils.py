from tron_utils import is_valid_tron_address


def test_valid_tron_address_passes_base58check():
    assert is_valid_tron_address("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb")


def test_changed_checksum_fails():
    assert not is_valid_tron_address("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwc")


def test_invalid_shape_fails():
    assert not is_valid_tron_address("")
    assert not is_valid_tron_address("T123")
    assert not is_valid_tron_address("A9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb")
    assert not is_valid_tron_address("T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWw0")
