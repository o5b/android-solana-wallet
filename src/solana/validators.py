import re

from solana.keypair import Keypair
from solana.publickey import PublicKey


def is_valid_wallet_name(name: str) -> bool:
    """
        Checks the validity of a wallet name.

        Args:
            name (str): The name of the wallet.

        Returns:
            bool: True if the wallet name is valid, False otherwise.
    """
    # регулярное выражение для проверки имени кошелька:
    # ^ - начало строки
    # [\w\d\s\-_]+ - один или более символов, которые могут быть буквами, цифрами, пробелами, дефисами или подчеркиваниями
    # $ - конец строки
    pattern = r'^[\w\d\s\-_]+$'
    return bool(re.match(pattern, name))


def is_valid_wallet_description(description: str) -> bool:
    """
        Checks the validity of a wallet description.

        Args:
            description (str): The description of the wallet.

        Returns:
            bool: True if the wallet description is valid, False otherwise.
    """
    if not description.strip():
        return False

    elif len(description) > 500:
        return False

    # Регулярное выражение для проверки допустимых символов:
    # буквы, цифры, пробелы, знаки препинания и некоторые специальные символы
    else:
        pattern = r'^[\w\d\s\-_,.:;!?]*$'
        return bool(re.match(pattern, description))


def is_valid_wallet_seed_phrase(seed_phrase: str) -> bool:
    """
        Checks the validity of a wallet seed phrase.

        Args:
            seed_phrase (str): The seed phrase of the wallet.

        Returns:
            bool: True if the wallet seed phrase is valid, False otherwise.
    """
    seed_phrase_list = seed_phrase.strip().split()

    if len(seed_phrase_list) in [12, 24]:
        return True
    else:
        return False


def is_valid_wallet_address(address: str) -> bool:
    """
        Checks whether the input string is a valid Solana wallet address.

        Args:
            address (str): A string containing the presumed wallet address.

        Returns:
            bool: True if the address is valid, False otherwise.
    """
    try:
        # PublicKey.from_string(address)
        PublicKey(address)
        # Если создание объекта PublicKey прошло успешно, значит адрес валиден
        return True
    except ValueError:
        # Если возникает ошибка, значит адрес невалиден, возвращаем False
        return False


# TODO: надо разобраться
def is_valid_private_key(private_key: str) -> bool:
    """
        Checks whether the input string is a valid Solana private key.

        Args:
            private_key (str): A string containing the presumed private key.

        Returns:
            bool: True if the private key is valid, False otherwise.
    """
    try:
        # Проверяем длину приватного ключа Solana
        # Если длина ключа 64 символа, это hex-представление
        if len(private_key) == 64:
            # Преобразование строки, содержащей приватный ключ, в байтовый формат с помощью метода fromhex,
            # а затем создание объекта Keypair из этих байтов.
            # Этот метод используется для создания объекта Keypair, который может быть использован для
            # подписывания транзакций или выполнения других операций, связанных с приватным ключом.
            Keypair.from_seed(bytes.fromhex(private_key))

        # Если длина ключа 32 символа, это представление в бинарном формате
        elif len(private_key) == 32:
            # Преобразование строки, содержащей приватный ключ, в байтовый формат с помощью метода fromhex,
            # а затем создание объекта Keypair из этих байтов.
            # Этот метод используется для создания объекта Keypair из приватного ключа с использованием его seed.
            Keypair.from_seed(bytes.fromhex(private_key))
        else:
            # Если длина ключа не соответствует ожидаемой длине, возвращаем False
            return False
        # Если создание объекта Keypair прошло успешно, значит приватный ключ валиден
        return True
    except ValueError:
        # Если возникает ошибка, значит приватный ключ невалиден, возвращаем False
        return False


def is_valid_amount(amount: str | int | float) -> bool:
    """
        Checks if the value is a valid amount.

        Arguments:
        amount (str | int | float): The value of the amount to be checked.

        Returns:
        bool: True if the amount value is valid, False otherwise.
    """
    # if isinstance(amount, (int, float)):
    #     return True
    #     # Если amount не является int или float, пытаемся преобразовать его в float.
    try:
        float(amount)
        return True
    except ValueError:
        return False
