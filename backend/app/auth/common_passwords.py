"""Passwords that are guessed first in every attack, so a new account may not use them.

Only entries of at least 8 characters are listed, since shorter passwords are refused
for their length anyway. Compared case-insensitively (see password_policy). It is a
short built-in list rather than a download so that sign-up needs no network.
"""

_WORDS = (
    # digit and keyboard sequences
    "12345678 123456789 1234567890 12345678910 123123123 1231231234 12341234 123412345 "
    "11111111 111111111 1111111111 22222222 33333333 44444444 55555555 66666666 77777777 "
    "88888888 99999999 00000000 000000000 0000000000 87654321 987654321 9876543210 "
    "12121212 11223344 1122334455 112233445566 123321123 123456123456 1234567891 "
    "123456789a 123456789q 12345678a 12345678q 1234567a a1234567 a12345678 a123456789 "
    "0123456789 0987654321 0912345678 0123456789a abc12345 abcd1234 abcd12345 abc123456 "
    "abcdefgh abcdefghi abcdefghij abcdefg1 1q2w3e4r 1q2w3e4r5t 1q2w3e4r5t6y 1qaz2wsx "
    "1qazxsw2 1qaz2wsx3edc q1w2e3r4 q1w2e3r4t5 qwertyui qwertyuiop qwertyuiop123 "
    "qwerty12 qwerty123 qwerty1234 qwerty12345 qwerty123456 qwertyu1 qwertyuio "
    "asdfghjk asdfghjkl asdfgh123 asdf1234 asdfasdf zxcvbnm1 zxcvbnm123 zaq12wsx "
    "zaq1zaq1 qazwsxedc qazwsx123 "
    # variations of the word password
    "password password1 password2 password12 password123 password1234 password12345 "
    "password2020 password2021 password2022 password2023 password2024 password2025 "
    "password2026 passw0rd passw0rd1 p@ssw0rd p@ssword p@ssword1 p@ssw0rd1 pa$$w0rd "
    "pass1234 pass12345 pass123456 passpass passpass1 mypassword mypassword1 mypass123 "
    "mypassword123 yourpassword newpassword newpass123 changeme changeme1 changeme123 "
    "default123 letmein1 letmein12 letmein123 welcome1 welcome12 welcome123 welcome1234 "
    "welcome2024 welcome2025 welcome2026 secret123 secret1234 secretsecret admin123 "
    "admin1234 admin12345 adminadmin administrator admin@123 admin2024 admin2025 "
    "admin2026 root1234 rootroot toor1234 login123 test1234 test12345 testtest "
    "testing123 guest123 user1234 username1 "
    # words and names people pick
    "iloveyou iloveyou1 iloveyou2 iloveyou12 iloveyou123 loveyou1 lovelove love1234 "
    "lovers123 sunshine sunshine1 princess princess1 football football1 baseball "
    "baseball1 basketball superman superman1 batman123 spiderman starwars trustno1 "
    "monkey123 dragon123 master123 shadow123 michael1 jennifer jessica1 charlie1 "
    "hunter12 jordan23 liverpool arsenal1 chelsea1 barcelona manchester computer "
    "computer1 internet internet1 flower123 butterfly freedom1 whatever whatever1 "
    "nothing1 blink182 pokemon1 naruto123 google123 facebook1 facebook123 youtube123 "
    "iphone123 samsung123 hello123 hello1234 hello12345 hello2024 hello2025 hello2026 "
    "qwerty2024 qwerty2025 qwerty2026 abc123456789 "
    # Vietnamese words and the university
    "matkhau matkhau1 matkhau12 matkhau123 matkhau1234 matkhau12345 matkhaucuatoi "
    "taikhoan1 taikhoan123 vietnam123 vietnam1 vietnam2024 vietnam2025 vietnam2026 "
    "hanoi123 saigon123 sinhvien1 sinhvien123 giaovien1 giaovien123 hocsinh123 "
    "hcmut123 hcmut1234 hcmut2024 hcmut2025 hcmut2026 bachkhoa bachkhoa1 bachkhoa123 "
    "dhbk1234 dhbkhcm1 anhyeuem anhyeuem1 emyeuanh emyeuanh1 yeuem123 yeuanh123 "
    "yeuvietnam "
)

COMMON_PASSWORDS = frozenset(_WORDS.split())
