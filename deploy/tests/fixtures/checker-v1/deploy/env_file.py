"""Supported single-line systemd EnvironmentFile values, without shell execution.

Full multiline/concatenated syntax is deliberately refused rather than guessed.
Errors never echo keys' values. Quotes/escapes do not become part of a token.
"""
from pathlib import Path
import re
import sys


class EnvError(ValueError):
    pass


def parse(data):
    result = {}
    for line in data.decode('utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith(('#', ';')):
            continue
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*)=(.*)', line)
        if match is None or match[1] in result or '\x00' in line or '\ufeff' in line:
            raise EnvError('Niejednoznaczny EnvironmentFile; wymagane pojedyncze KEY=value.')
        value = match[2].strip(' \t\r')
        quote = value[0] if value[:1] in ("'", '"') else None
        content = value[1:] if quote else value
        output = []
        index = 0
        closed = not quote
        while index < len(content):
            char = content[index]
            if quote and char == quote:
                if content[index + 1:].strip(' \t\r'):
                    raise EnvError('Nieobsługiwana konkatenacja EnvironmentFile.')
                closed = True
                break
            if char == '\\' and quote != "'":
                index += 1
                if index == len(content):
                    raise EnvError('Wieloliniowy EnvironmentFile wymaga osobnej konfiguracji.')
                char = content[index]
                if quote == '"' and char not in '\\"`$':
                    output.append('\\')
            output.append(char)
            index += 1
        if not closed:
            raise EnvError('Niedomknięty cudzysłów EnvironmentFile.')
        result[match[1]] = ''.join(output)
    return result


if __name__ == '__main__':
    try:
        # Captured by the shell helper, never logged or passed as a CLI value.
        print(parse(Path(sys.argv[1]).read_bytes())[sys.argv[2]], end='')
    except KeyError:
        raise SystemExit(1)
    except (OSError, ValueError, IndexError):
        print('Nie można odczytać klucza EnvironmentFile (wartości ukryte).', file=sys.stderr)
        raise SystemExit(1)
