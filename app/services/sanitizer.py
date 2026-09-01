import re
from typing import Dict, List, Tuple, Union
from app.schemas.proxy import ChatMessage


def luhn_checksum_valid(card_str: str) -> bool:
    """
    Validates a candidate credit card number string using the Luhn checksum algorithm.
    """
    digits = [int(c) for c in card_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, digit in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = digit * 2
            checksum += doubled if doubled < 10 else doubled - 9
        else:
            checksum += digit
    return checksum % 10 == 0


class PIISanitizer:
    """
    High-performance PII and secrets redactor with reversible surrogate token mapping.
    Target execution latency: < 10ms.
    """

    # Pre-compiled high-efficiency regular expressions
    _EMAIL_REGEX = re.compile(
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'
    )
    _IPV4_REGEX = re.compile(
        r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'
    )
    _AWS_KEY_REGEX = re.compile(
        r'\b(AKIA[0-9A-Z]{16})\b'
    )
    _OPENAI_KEY_REGEX = re.compile(
        r'\b(sk-[A-Za-z0-9_-]{20,})\b'
    )
    _JWT_REGEX = re.compile(
        r'\b(eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b'
    )
    _BEARER_REGEX = re.compile(
        r'\bBearer\s+([A-Za-z0-9\-\._~\+\/]+=*)', re.IGNORECASE
    )
    _PHONE_REGEX = re.compile(
        r'(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b'
    )
    _CARD_CANDIDATE_REGEX = re.compile(
        r'\b(?:\d[ -]*?){13,19}\b'
    )

    def sanitize(self, text: str) -> Tuple[str, Dict[str, str]]:
        """
        Sanitizes sensitive entities in a single string, returning the scrubbed text
        and a surrogate lookup mapping dictionary ({ "[REDACTED_EMAIL_1]": "user@domain.com" }).
        """
        if not text:
            return text, {}

        mapping: Dict[str, str] = {}
        reverse_mapping: Dict[str, str] = {}
        counters: Dict[str, int] = {
            "EMAIL": 0,
            "CREDIT_CARD": 0,
            "PHONE": 0,
            "SECRET": 0,
            "IPV4": 0,
        }

        def get_surrogate(category: str, raw_value: str) -> str:
            if raw_value in reverse_mapping:
                return reverse_mapping[raw_value]
            counters[category] += 1
            surrogate = f"[REDACTED_{category}_{counters[category]}]"
            mapping[surrogate] = raw_value
            reverse_mapping[raw_value] = surrogate
            return surrogate

        scrubbed = text

        # 1. AWS Access Keys
        for match in self._AWS_KEY_REGEX.finditer(text):
            raw = match.group(1)
            surrogate = get_surrogate("SECRET", raw)
            scrubbed = scrubbed.replace(raw, surrogate)

        # 2. OpenAI / Provider Keys
        for match in self._OPENAI_KEY_REGEX.finditer(text):
            raw = match.group(1)
            surrogate = get_surrogate("SECRET", raw)
            scrubbed = scrubbed.replace(raw, surrogate)

        # 3. JWT Tokens
        for match in self._JWT_REGEX.finditer(text):
            raw = match.group(1)
            surrogate = get_surrogate("SECRET", raw)
            scrubbed = scrubbed.replace(raw, surrogate)

        # 4. Bearer Tokens
        for match in self._BEARER_REGEX.finditer(text):
            full_match = match.group(0)
            token_val = match.group(1)
            surrogate = get_surrogate("SECRET", token_val)
            scrubbed = scrubbed.replace(full_match, f"Bearer {surrogate}")

        # 5. Email Addresses
        for match in self._EMAIL_REGEX.finditer(text):
            raw = match.group(0)
            surrogate = get_surrogate("EMAIL", raw)
            scrubbed = scrubbed.replace(raw, surrogate)

        # 6. Credit Cards (Luhn verified)
        for match in self._CARD_CANDIDATE_REGEX.finditer(text):
            raw = match.group(0)
            clean_digits = "".join(c for c in raw if c.isdigit())
            if luhn_checksum_valid(clean_digits):
                surrogate = get_surrogate("CREDIT_CARD", raw)
                scrubbed = scrubbed.replace(raw, surrogate)

        # 7. IPv4 Addresses
        for match in self._IPV4_REGEX.finditer(text):
            raw = match.group(0)
            # Avoid matching inside surrogate tokens or previously redacted elements
            if raw in scrubbed and not any(raw in val for val in mapping.values()):
                surrogate = get_surrogate("IPV4", raw)
                scrubbed = scrubbed.replace(raw, surrogate)

        # 8. Phone Numbers (excluding already redacted elements and plain small integers)
        for match in self._PHONE_REGEX.finditer(scrubbed):
            raw = match.group(0).strip()
            digits_only = "".join(c for c in raw if c.isdigit())
            if 7 <= len(digits_only) <= 15:
                # Check that it's not part of an existing surrogate
                if not raw.startswith("[REDACTED_") and raw in scrubbed:
                    surrogate = get_surrogate("PHONE", raw)
                    scrubbed = scrubbed.replace(raw, surrogate)

        return scrubbed, mapping

    def sanitize_messages(self, messages: List[ChatMessage]) -> Tuple[List[ChatMessage], Dict[str, str], int]:
        """
        Sanitizes a list of ChatMessage instances.
        Returns (sanitized_messages, aggregated_mapping, total_redactions_count).
        """
        aggregated_mapping: Dict[str, str] = {}
        sanitized_messages: List[ChatMessage] = []
        total_redacted = 0

        for msg in messages:
            if isinstance(msg.content, str):
                scrubbed_content, msg_map = self.sanitize(msg.content)
                total_redacted += len(msg_map)
                aggregated_mapping.update(msg_map)
                sanitized_messages.append(
                    ChatMessage(role=msg.role, content=scrubbed_content, name=msg.name)
                )
            else:
                sanitized_messages.append(msg)

        return sanitized_messages, aggregated_mapping, total_redacted

    def restore(self, text: str, mapping: Dict[str, str]) -> str:
        """
        Re-substitutes original sensitive entities into the text output using the lookup mapping.
        """
        if not text or not mapping:
            return text

        restored = text
        for surrogate, original_value in mapping.items():
            restored = restored.replace(surrogate, original_value)

        return restored


sanitizer = PIISanitizer()
