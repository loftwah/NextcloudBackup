from Crypto.Protocol.KDF import PBKDF2
from Crypto.Cipher import AES
from io import BytesIO
from .CompressUtils import compress_file, decompress_file
import struct, os, zlib, hashlib

PBKDF_SALT = b'\xd7\x84\xcd\xcbl39\x98\xca\x92\x97fH\x86\x9e\xa1f\xef\xb9\xee\x8b\xab\xb3N\xd6\xf4\x17\xda\xba\xb6\xbe\xc1'
VERSION = 0

def derive_key(contents, length=16):
    return PBKDF2(contents, PBKDF_SALT, dkLen=length)

def read_struct(input, type):
    return struct.unpack(type, input.read(struct.calcsize(type)))[0]

def encrypt_chunk(aes, chunk):
    if len(chunk) % 16 != 0:
        chunk += b' ' * (16 - len(chunk) % 16)

    return aes.encrypt(chunk)

def combine_files(input_filenames, output_filename, base_folder, encrypted_folder, file_password, chunk_size=64*1024):
    output_basename = os.path.basename(output_filename)
    key = derive_key(file_password + output_basename, 32)
    iv = os.urandom(16)
    aes = AES.new(key, AES.MODE_CBC, iv)
    file_headers = []
    combined_files = {}

    files_only = os.path.join(encrypted_folder, output_basename + '-files')

    with open(files_only, 'wb') as output:
        for filename, timestamp in input_filenames.items():
            timestamp = int(timestamp)
            drive_path = os.path.join(base_folder, filename)

            if not os.path.exists(drive_path):
                continue

            version_hash = hashlib.sha256(filename.encode('utf-8')).hexdigest()
            compressed_path = os.path.join(encrypted_folder, version_hash + '-compressed')

            compress_file(drive_path, compressed_path)
            compressed_size = os.path.getsize(compressed_path)

            start_position = output.tell()
            faes = AES.new(key, AES.MODE_CBC, iv)

            with open(compressed_path, 'rb') as input:
                while True:
                    chunk = input.read(chunk_size)

                    if len(chunk) == 0:
                        break

                    output.write(encrypt_chunk(faes, chunk))

            os.remove(compressed_path)

            end_position = output.tell()
            header = b''
            header += struct.pack('<Q', timestamp)
            header += struct.pack('<H', len(filename))
            header += filename.encode()
            header += struct.pack('<Q', os.path.getsize(drive_path))
            header += struct.pack('<Q', compressed_size)
            header += struct.pack('<Q', start_position)
            header += struct.pack('<Q', end_position)
            file_headers.append(header)
            combined_files[filename] = timestamp

    header = encrypt_chunk(aes, b''.join(file_headers))

    with open(output_filename, 'wb') as output:
        output_header = b''
        output_header += struct.pack('<B', VERSION)
        output_header += struct.pack('<B', len(iv))
        output_header += struct.pack('<H', len(file_headers))
        output_header += struct.pack('<I', len(header))
        output_header += iv
        output_header += header
        output_header += struct.pack('<I', len(output_header) + struct.calcsize('<I'))
        output.write(output_header)

        with open(files_only, 'rb') as input:
            while True:
                chunk = input.read(chunk_size)

                if not chunk:
                    break

                output.write(chunk)

        os.remove(files_only)

    return combined_files, len(output_header)

def read_headers(input_filename, file_password, chunk_size=64*1024):
    key = derive_key(file_password + os.path.basename(input_filename), 32)
    files = {}

    with open(input_filename, 'rb') as input:
        version = read_struct(input, '<B')

        if version != VERSION:
            raise Exception('Cloud file {0} ({1}) has invalid version.'.format(input_filename, output_filename))

        iv_length = read_struct(input, '<B')
        file_count = read_struct(input, '<H')
        header_length = read_struct(input, '<I')
        iv = input.read(iv_length)

        aes = AES.new(key, AES.MODE_CBC, iv)
        header = input.read(header_length)
        header = aes.decrypt(header)

        file_seek = read_struct(input, '<I')

        with BytesIO(header) as header:
            for i in range(file_count):
                timestamp = read_struct(header, '<Q')
                filename_length = read_struct(header, '<H')
                filename = header.read(filename_length).decode()
                original_size = read_struct(header, '<Q')
                compressed_size = read_struct(header, '<Q')
                start_seek = read_struct(header, '<Q')
                end_seek = read_struct(header, '<Q')
                files[filename] = {'time': timestamp, 'size': original_size, 'compressed': compressed_size, 'start_seek': start_seek, 'end_seek': end_seek}

    return {'files': files, 'iv': iv, 'file_seek': file_seek}

def decrypt_files(input_filename, files, file_password, encrypted_folder, base_folder, headers=None, chunk_size=64*1024):
    if not headers:
        headers = read_headers(input_filename, file_password, chunk_size)

    key = derive_key(file_password + os.path.basename(input_filename), 32)
    all_files = headers['files']
    file_seek = headers['file_seek']
    iv = headers['iv']

    with open(input_filename, 'rb') as input:
        for filename in files:
            file = all_files[filename]
            file_length = file['end_seek'] - file['start_seek']
            start_seek = file_seek + file['start_seek']

            version_hash = hashlib.sha256(filename.encode('utf-8')).hexdigest()
            compressed_path = os.path.join(encrypted_folder, version_hash + '-decompressed')
            input.seek(start_seek)

            with open(compressed_path, 'wb') as output:
                aes = AES.new(key, AES.MODE_CBC, iv)

                while True:
                    chunk = input.read(min(chunk_size, file_length))

                    if len(chunk) == 0:
                        break

                    output.write(aes.decrypt(chunk))
                    file_length -= chunk_size

                    if file_length <= 0:
                        break

                output.truncate(file['compressed'])

            decompress_file(compressed_path, os.path.join(base_folder, filename))
            os.remove(compressed_path)

def decrypt_file(input_filename, output_filename, key, chunk_size=64*1024):
    with open(input_filename, 'rb') as input:
        version = read_struct(input, '<B')

        if version != VERSION:
            raise Exception('Cloud file {0} ({1}) has invalid version.'.format(input_filename, output_filename))

        iv_length = read_struct(input, '<B')
        header_length = read_struct(input, '<I')
        iv = input.read(iv_length)

        aes = AES.new(key, AES.MODE_CBC, iv)

        header = input.read(header_length)
        header = aes.decrypt(header)

        with BytesIO(header) as header:
            timestamp = read_struct(header, '<Q')
            filename_length = read_struct(header, '<H')
            filename = header.read(filename_length).decode()
            original_size = read_struct(header, '<Q')

        with open(output_filename, 'wb') as output:
            while True:
                chunk = input.read(chunk_size)

                if len(chunk) == 0:
                    break

                output.write(aes.decrypt(chunk))

            output.truncate(original_size)

        return (filename, timestamp)
