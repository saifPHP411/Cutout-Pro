"""Stage a PeakCut admin backup in a new private directory."""
import argparse
import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from studio import backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("new_directory", type=Path)
    args = parser.parse_args()
    result = backup.recover_file(args.archive, args.new_directory, getpass.getpass("Backup passphrase: "))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
