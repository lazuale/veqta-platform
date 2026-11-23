from django.core.validators import RegexValidator


validate_text = RegexValidator(
    regex=r"[\x00\ud800-\udfff]",
    inverse_match=True,
    message="Текст содержит недопустимый символ",
)
