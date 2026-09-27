#!/usr/bin/env python3
"""
RSS Extractor v2.1 — для игры "Недетские сказки" (движок Saturn-plus)

Извлекает файлы из .rss-архивов.
WAV-файлы с формат-тегом Ogg Vorbis (0x674F и др.) конвертируются в .ogg
WAV-файлы со стандартными кодеками (PCM, ADPCM) сохраняются как .wav
Прочие файлы сохраняются как есть.

Использование:
  python rss_extractor_v2.py block12.rss extracted/
  python rss_extractor_v2.py block12.rss extracted/ -v
  python rss_extractor_v2.py --all "C:\Games\Недетские сказки" extracted/
  python rss_extractor_v2.py block12.rss extracted/ --dry-run -v
"""

import struct
import os
import sys
import argparse

# Все варианты Ogg Vorbis format tags в RIFF/WAV (из vorbis.acm)
VORBIS_TAGS = {
    0x674F: "Vorbis mode 1",
    0x6750: "Vorbis mode 2",
    0x6751: "Vorbis mode 3",
    0x676F: "Vorbis mode 1+",
    0x6770: "Vorbis mode 2+",
    0x6771: "Vorbis mode 3+",
}


def parse_rss_header(f):
    """Парсит заголовок .rss. Возвращает (список_записей, конец_заголовка)."""
    f.seek(0)
    count = struct.unpack('<I', f.read(4))[0]

    records = []
    pos = 4

    for i in range(count):
        f.seek(pos)
        record_data = f.read(92)
        if len(record_data) < 92:
            break

        # Извлекаем имя (первая null-terminated строка)
        nul = record_data.index(0) if 0 in record_data else 80
        name = record_data[:nul].decode('cp1251', errors='replace')

        # Метаданные в байтах [80:92]
        offset_val, size_val, block_val = struct.unpack('<III', record_data[80:92])

        records.append({
            'index': i,
            'name': name,
            'offset': offset_val,
            'size': size_val,
            'block': block_val,
        })
        pos += 92

    return records, pos


def parse_wav(data):
    """Парсит RIFF/WAV. Возвращает словарь с информацией о чанках или None."""
    if len(data) < 12 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        return None

    riff_size = struct.unpack('<I', data[4:8])[0]
    result = {
        'riff_size': riff_size,
        'total_riff': riff_size + 8,
        'format_tag': None,
        'channels': None,
        'sample_rate': None,
        'data_offset': None,
        'data_size': None,
    }

    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos+4]
        chunk_size = struct.unpack('<I', data[pos+4:pos+8])[0]
        chunk_start = pos + 8

        if chunk_id == b'fmt ' and chunk_size >= 16 and chunk_start + 16 <= len(data):
            (result['format_tag'], result['channels'],
             result['sample_rate'], avg_bps, block_align, bits) = \
                struct.unpack('<HHIIHH', data[chunk_start:chunk_start+16])

        elif chunk_id == b'data':
            result['data_offset'] = chunk_start
            result['data_size'] = chunk_size

        # Переход к следующему чанку (с выравниванием)
        pos = chunk_start + chunk_size
        if chunk_size % 2 != 0:
            pos += 1

    return result


def safe_path(name):
    """Создаёт безопасный путь для файловой системы."""
    name = name.replace('\\', '/')
    parts = [p for p in name.split('/') if p and p not in ('..', '.')]
    parts = [''.join(c for c in p if c not in '<>:"|?*') for p in parts]
    return os.path.join(*parts) if parts else 'unnamed'


def extract_rss(rss_path, output_dir, verbose=False, dry_run=False):
    """Извлекает все файлы из .rss архива."""
    stats = {
        'total': 0, 'extracted': 0, 'errors': 0,
        'vorbis': 0, 'vorbis_ogg_ok': 0, 'vorbis_no_oggs': 0,
        'pcm': 0, 'adpcm': 0, 'other_wav': 0, 'non_wav': 0,
        'truncated': 0,
    }

    with open(rss_path, 'rb') as f:
        records, header_end = parse_rss_header(f)
        stats['total'] = len(records)

        print(f"=== {os.path.basename(rss_path)} ({os.path.getsize(rss_path):,} байт) ===")
        print(f"Записей: {len(records)}, заголовок: {header_end:,} байт")
        print()

        for rec in records:
            name = rec['name']
            offset = rec['offset']
            size = rec['size']

            if verbose and rec['index'] % 1000 == 0 and rec['index'] > 0:
                print(f"  [{rec['index']}/{len(records)}]...")

            try:
                f.seek(offset)
                data = f.read(size)
                actual_size = len(data)

                if actual_size < size:
                    stats['truncated'] += 1
                    if verbose:
                        print(f"  УСЕЧЕНИЕ: {name} ({actual_size}/{size} байт)")

                wav = parse_wav(data)

                if wav and wav['format_tag'] is not None:
                    fmt = wav['format_tag']

                    if fmt in VORBIS_TAGS:
                        # Ogg Vorbis в WAV-контейнере → извлекаем как .ogg
                        stats['vorbis'] += 1

                        if wav['data_offset'] is not None and wav['data_size'] is not None:
                            ogg = data[wav['data_offset']:wav['data_offset'] + wav['data_size']]

                            # Проверяем сигнатуру OggS
                            if ogg[:4] == b'OggS':
                                stats['vorbis_ogg_ok'] += 1
                            else:
                                stats['vorbis_no_oggs'] += 1
                                if verbose:
                                    print(f"  ВНИМАНИЕ: {name} — данные не начинаются с OggS "
                                          f"(первые байты: {ogg[:8].hex(' ')})")

                            out_name = name.rsplit('.', 1)[0] + '.ogg'
                            out_path = os.path.join(output_dir, safe_path(out_name))

                            if not dry_run:
                                os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
                                with open(out_path, 'wb') as out_f:
                                    out_f.write(ogg)
                            stats['extracted'] += 1
                        else:
                            stats['errors'] += 1
                            if verbose:
                                print(f"  ОШИБКА: нет data-чанка в {name}")

                    elif fmt in (1, 2, 3):  # PCM, ADPCM, IEEE Float
                        if fmt == 1: stats['pcm'] += 1
                        elif fmt == 2: stats['adpcm'] += 1
                        else: stats['other_wav'] += 1

                        out_path = os.path.join(output_dir, safe_path(name))
                        # Сохраняем с корректным размером
                        correct = min(wav['total_riff'], actual_size)

                        if not dry_run:
                            os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
                            with open(out_path, 'wb') as out_f:
                                out_f.write(data[:correct])
                        stats['extracted'] += 1

                    else:
                        stats['other_wav'] += 1
                        out_path = os.path.join(output_dir, safe_path(name))
                        if not dry_run:
                            os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
                            with open(out_path, 'wb') as out_f:
                                out_f.write(data)
                        stats['extracted'] += 1
                        if verbose:
                            print(f"  Неизвестный кодек 0x{fmt:04X}: {name}")

                else:
                    # Не-WAV файл
                    stats['non_wav'] += 1
                    out_path = os.path.join(output_dir, safe_path(name))
                    if not dry_run:
                        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
                        with open(out_path, 'wb') as out_f:
                            out_f.write(data)
                    stats['extracted'] += 1

            except Exception as e:
                stats['errors'] += 1
                if verbose:
                    print(f"  ОШИБКА: {name}: {e}")

    print(f"\n=== Итог ===")
    print(f"Всего:              {stats['total']}")
    print(f"Извлечено:          {stats['extracted']}")
    print(f"Ошибок:             {stats['errors']}")
    print(f"  Vorbis → .ogg:    {stats['vorbis']} (с сигнатурой OggS: {stats['vorbis_ogg_ok']}, без: {stats['vorbis_no_oggs']})")
    print(f"  PCM WAV:          {stats['pcm']}")
    print(f"  ADPCM WAV:        {stats['adpcm']}")
    print(f"  Другие WAV:        {stats['other_wav']}")
    print(f"  Не-WAV файлы:     {stats['non_wav']}")
    if stats['truncated']:
        print(f"  Усечённых файлов: {stats['truncated']}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description='Извлечение ресурсов из .rss архивов "Недетские сказки"')
    parser.add_argument('rss_file', help='Путь к .rss файлу или папке (с --all)')
    parser.add_argument('output_dir', nargs='?', default='extracted',
                        help='Папка для извлечённых файлов (по умолчанию: extracted)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Подробный вывод')
    parser.add_argument('-n', '--dry-run', action='store_true',
                        help='Только анализ, без записи файлов')
    parser.add_argument('-a', '--all', action='store_true',
                        help='Обработать все .rss файлы в указанной папке')

    args = parser.parse_args()

    if args.all:
        rss_dir = args.rss_file
        if not os.path.isdir(rss_dir):
            print(f"Папка не найдена: {rss_dir}")
            return
        rss_files = sorted(f for f in os.listdir(rss_dir) if f.lower().endswith('.rss'))
        if not rss_files:
            print(f"В папке {rss_dir} нет .rss файлов")
            return
        print(f"Найдено {len(rss_files)} .rss файлов\n")
        for rss_file in rss_files:
            rss_path = os.path.join(rss_dir, rss_file)
            extract_rss(rss_path, args.output_dir, verbose=args.verbose, dry_run=args.dry_run)
            print()
    else:
        if not os.path.isfile(args.rss_file):
            print(f"Файл не найден: {args.rss_file}")
            return
        extract_rss(args.rss_file, args.output_dir, verbose=args.verbose, dry_run=args.dry_run)


if __name__ == '__main__':
    main()
