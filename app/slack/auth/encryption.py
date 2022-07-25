import codecs
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding


def encrypt_aes(data: str, key: bytes) -> str:
    """Encrypts a string using AES encryption, ECB mode and PKCS7 padding.

    This function is compatible with Lucee's encrypt() function.

    Args:
        data (str): The string to encrypt.
        key (bytes): The encryption key.

    Returns:
        str: The encrypted string in hex encoding.
    """
    aes = algorithms.AES(key)
    padder = padding.PKCS7(aes.block_size).padder()
    padded_data = padder.update(data.encode()) + padder.finalize()

    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
    return codecs.encode(encrypted_data, 'hex').decode()


def decrypt_aes(data: str, key: bytes) -> str:
    """Decrypts a hex encoded string using AES encryption, ECB mode and PKCS7 padding.

    This function is compatible with Lucee's decrypt() function.

    Args:
        data (str): The string to decrypt (in hex encoding).
        key (bytes): The encryption key.

    Returns:
        str: The decrypted string.
    """
    aes = algorithms.AES(key)
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    decrypted_padded_data = decryptor.update(
        codecs.decode(data.encode(), 'hex')) + decryptor.finalize()

    padder = padding.PKCS7(aes.block_size).unpadder()
    decrypted_data = padder.update(decrypted_padded_data) + padder.finalize()
    return decrypted_data.decode()
