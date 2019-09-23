from EncryptedSettings import EncryptedSettings
from CompressUtils import decompress_file
from CryptoUtils import derive_key, decrypt_file
from GDrive import GDrive

import json, requests, os, sys, time, hashlib, shutil, traceback

class DecryptTool(object):

    def __init__(self):
        self.webhook_url = None
        self.warnings = []

        try:
            self.read_settings()
        except:
            self.complain_and_exit('Could not read settings!')

        try:
            self.connect_to_google()
        except:
            self.complain_and_exit('Could not connect to Google Drive!')

        self.decrypted_folder = self.create_decrypted_folder()

        try:
            self.restore_manifest()
        except:
            self.complain_and_exit('Could not restore manifest!')

        try:
            self.restore_all()
        except:
            self.complain_and_exit('Could not complete backup!')

        if self.warnings:
            self.send_webhook('\n'.join(self.warnings))

    def warn(self, message):
        self.warnings.append(message)
        print(message)

    def send_webhook(self, message, urgent=False):
        if urgent:
            message = '@everyone ' + message

        max_length = 19980

        for message in [message[i:i+max_length] for i in range(0, len(message), max_length)]:
            requests.post(self.webhook_url, headers={'User-Agent': 'Mozilla/5.0'}, data={'content': message})

    def complain_and_exit(self, message):
        exception = traceback.format_exc()

        print(message)
        print(exception)

        if self.webhook_url:
            self.send_webhook('{0}\n```{1}```'.format(message, exception), urgent=True)

        sys.exit()

    def read_settings(self):
        with open('settings.json', 'r') as f:
            self.settings = json.load(f)

        self.file_password = self.settings['file_password']
        self.backup_folder_id = self.settings['backup_folder_id']
        self.webhook_url = self.settings['webhook_url']

    def connect_to_google(self):
        self.drive = GDrive(self.settings['team_drive_id'])
        self.drive.connect()

    def create_decrypted_folder(self):
        decrypted_folder = os.path.join(os.getcwd(), 'decrypted')

        if not os.path.exists(decrypted_folder):
            os.makedirs(decrypted_folder)

        return decrypted_folder

    def remove_file_discreetly(self, filename):
        while True:
            try:
                if os.path.exists(filename):
                    os.remove(filename)

                return
            except:
                time.sleep(0.1)

    def restore_manifest(self):
        self.hash_folders = self.drive.list_folders_in(self.backup_folder_id)
        manifest_folder = self.drive.find_file_in_list(self.hash_folders, 'manifests')

        if not manifest_folder:
            raise Exception('No manifests are available!')

        manifests = self.drive.list_files_in(manifest_folder['id'])

        if not manifests:
            raise Exception('No manifests are available!')

        manifests.sort(key=lambda file: file['modifiedDate'], reverse=True)
        manifest = manifests[0]
        manifest_path = os.path.join(self.decrypted_folder, manifest['title'])

        manifest.GetContentFile(manifest_path)

        self.manifest = EncryptedSettings(manifest_path, self.settings['manifest_password'])

    def restore_file(self, filename, file_info):
        if not file_info['active']:
            print('{0} is inactive, not downloading.'.format(filename))
            return

        drive_path = os.path.join(self.decrypted_folder, filename)
        versions = file_info['versions']

        if not versions:
            self.warn('File {0} has no versions!'.format(filename))
            return

        latest_version = max(versions.keys())
        version_info = versions[latest_version]

        if os.path.exists(drive_path) and os.path.getsize(drive_path) == version_info['size']:
            return

        version_hash = version_info['hash']
        print('Looking for {0}, version {1}...'.format(filename, latest_version))

        file = self.drive.search_for_file(version_hash)

        if not file:
            self.warn('File {0} version {1} is missing!'.format(filename, latest_version))
            return

        if len(file) > 1:
            self.warn('Hash {0} belonging to {1} appears more than once.'.format(version_hash, filename))

        file = file[0]
        dir = os.path.dirname(drive_path)

        if not os.path.exists(dir):
            os.makedirs(dir)

        print('Downloading...')
        encrypted_path = drive_path + '.enc'
        compressed_path = drive_path + '.com'
        file.GetContentFile(encrypted_path)

        if os.path.getsize(encrypted_path) != version_info['encryptedSize']:
            self.warn('File {0} has unexpected encrypted size: {1} (expected {2})'.format(filename, os.path.getsize(encrypted_path), version_info['encryptedSize']))
            return

        print('Decrypting...')
        key = derive_key(self.file_password + version_hash, 32)
        decrypt_file(key, encrypted_path, compressed_path)
        self.remove_file_discreetly(encrypted_path)

        if os.path.getsize(compressed_path) != version_info['compressedSize']:
            self.warn('File {0} has unexpected encrypted final size: {1} (expected {2})'.format(filename, os.path.getsize(drive_path), version_info['compressedSize']))
            return

        decompress_file(compressed_path, drive_path)
        self.remove_file_discreetly(compressed_path)

    def restore_all(self):
        for filename, file_info in self.manifest['files'].items():
            self.restore_file(filename, file_info)

if __name__ == '__main__':
    DecryptTool()
