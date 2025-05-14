#!/usr/bin/env python3
import os
import sys
import csv
import logging
import subprocess
import argparse
from pathlib import Path
from typing import List, Tuple, Dict

# Конфигурация
DEFAULT_CONFIG = {
    'iso_dir': '/mnt/iso',
    'backup_dir': '/mnt/backup',
    'unpack_dir': '/mnt/share',
    'mount_dir': '/media',
    'default_inv_file': 'inventory.numbers.csv',
    'log_file': 'distr_build.log',
    'exclude_files': ['.gitignore', 'README.md', 'distr_build.*', 'inventory.numbers.*']
}

class DistrBuilder:
    def __init__(self, config: Dict):
        self.config = config
        self.mounted_points = []
        self.loop_devices = []
        self._setup_logging()

    def _setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.config['log_file']),
                logging.StreamHandler()
            ]
        )

    def mount_smb(self, share: str, mount_point: str):
        try:
            subprocess.run(
                ['mount.cifs', share, mount_point],
                check=True,
                capture_output=True,
                text=True
            )
            logging.info(f"Mounted {share} to {mount_point}")
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to mount {share}: {e.stderr}")
            raise

    def process_inventory(self, inv_file: str):
        inv_path = Path(inv_file)
        if not inv_path.exists():
            logging.error(f"Inventory file {inv_file} not found!")
            sys.exit(1)

        with open(inv_path, 'r') as f:
            reader = csv.reader(f, delimiter=';')
            for pattern, mask, target_dir in reader:
                self.process_pattern(pattern.strip(), mask.strip(), target_dir.strip())

    def process_pattern(self, pattern: str, mask: str, target_dir: str):
        full_target = Path(self.config['unpack_dir']) / target_dir
        full_target.mkdir(parents=True, exist_ok=True)

        if pattern:
            search_path = Path(self.config['mount_dir'])
            found_files = list(search_path.glob(f"{pattern}*/**/{mask}"))
        else:
            found_files = list(Path(self.config['backup_dir']).glob(mask))

        if not found_files:
            logging.warning(f"No files found for pattern: {pattern}*/{mask}")
            return

        for file in found_files:
            self.copy_file(file, full_target)

    def copy_file(self, src: Path, dst_dir: Path):
        try:
            subprocess.run(
                ['rsync', '-ah', '--bwlimit=102400', str(src), str(dst_dir)],
                check=True
            )
            logging.info(f"Copied {src} to {dst_dir}")
        except subprocess.CalledProcessError:
            logging.error(f"Failed to copy {src}")

    def mount_iso(self, iso_path: Path):
        mount_point = Path(self.config['mount_dir']) / iso_path.stem
        mount_point.mkdir(exist_ok=True)

        if self.is_mounted(mount_point):
            logging.info(f"{mount_point} already mounted")
            return

        try:
            if self.is_iso9660(iso_path):
                subprocess.run(
                    ['fuseiso', '-o', 'ro', str(iso_path), str(mount_point)],
                    check=True
                )
                self.mounted_points.append(mount_point)
            else:
                loop_dev = self.create_loop(iso_path)
                subprocess.run(
                    ['pmount', '-r', loop_dev, str(mount_point)],
                    check=True
                )
                self.mounted_points.append(mount_point)
                self.loop_devices.append(loop_dev)

            logging.info(f"Mounted {iso_path} to {mount_point}")

        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to mount {iso_path}: {e}")

    def cleanup(self):
        self.umount_all()
        self.remove_loop_devices()

    def umount_all(self):
        for mp in reversed(self.mounted_points):
            try:
                subprocess.run(['fusermount', '-u', str(mp)], check=True)
                mp.rmdir()
                logging.info(f"Unmounted {mp}")
            except subprocess.CalledProcessError:
                logging.warning(f"Failed to unmount {mp}")

    def remove_loop_devices(self):
        for dev in self.loop_devices:
            try:
                subprocess.run(['losetup', '-d', dev], check=True)
                logging.info(f"Removed loop device {dev}")
            except subprocess.CalledProcessError:
                logging.warning(f"Failed to remove loop device {dev}")

    @staticmethod
    def is_mounted(path: Path) -> bool:
        result = subprocess.run(
            ['mountpoint', '-q', str(path)],
            capture_output=True
        )
        return result.returncode == 0

    @staticmethod
    def is_iso9660(path: Path) -> bool:
        result = subprocess.run(
            ['isoinfo', '-i', str(path)],
            capture_output=True
        )
        return result.returncode == 0

    @staticmethod
    def create_loop(path: Path) -> str:
        result = subprocess.run(
            ['losetup', '--show', '-f', str(path)],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()

def main():
    parser = argparse.ArgumentParser(description='Distribution builder')
    parser.add_argument('--inv', help='Inventory version')
    args = parser.parse_args()

    config = DEFAULT_CONFIG.copy()
    config['inv_file'] = (
        f"inventory.numbers.{args.inv}.csv" 
        if args.inv else 
        DEFAULT_CONFIG['default_inv_file']
    )

    builder = DistrBuilder(config)
    
    try:
        # Монтирование ресурсов
        builder.mount_smb("//sp-dk-smb.seaproject.ru/project_iso", config['iso_dir'])
        builder.mount_smb("//sp-dk-smb.seaproject.ru/project_backup", config['backup_dir'])
        
        # Обработка файлов
        builder.process_inventory(config['inv_file'])
        
        # Очистка
        builder.cleanup()
        
        logging.info("Operation completed successfully")
    except Exception as e:
        logging.error(f"Critical error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
