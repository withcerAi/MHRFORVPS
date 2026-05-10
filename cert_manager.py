#!/usr/bin/env python3
"""
Simple certificate manager for DomainFront Tunnel

Install / uninstall MITM CA certificate
"""

import os
import sys
import logging

# add src to path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from cert_installer import install_ca, uninstall_ca, is_ca_trusted
from mitm import CA_CERT_FILE, MITMCertManager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("CertManager")


def ensure_cert_exists():
    if not os.path.exists(CA_CERT_FILE):
        log.info("Generating CA certificate...")
        MITMCertManager()


def install():
    ensure_cert_exists()

    if is_ca_trusted(CA_CERT_FILE):
        log.info("CA is already trusted ✔")
        return

    log.info("Installing CA certificate...")
    ok = install_ca(CA_CERT_FILE)

    if ok:
        log.info("✔ Certificate installed successfully")
        log.info("⚠ Restart your browser!")
    else:
        log.error("❌ Failed to install certificate")


def uninstall():
    if not os.path.exists(CA_CERT_FILE):
        log.warning("CA file not found")
        return

    log.info("Removing CA certificate...")
    ok = uninstall_ca(CA_CERT_FILE)

    if ok:
        log.info("✔ Certificate removed")
    else:
        log.warning("⚠ Removal may have failed")


def status():
    if not os.path.exists(CA_CERT_FILE):
        print("❌ Certificate not generated")
        return

    if is_ca_trusted(CA_CERT_FILE):
        print("✔ Trusted")
    else:
        print("⚠ Not trusted")


def main():
    print("")
    print("=== Certificate Manager ===")
    print("1) Install certificate")
    print("2) Uninstall certificate")
    print("3) Status")
    print("")

    choice = input("Choose [1/2/3]: ").strip()

    if choice == "1":
        install()
    elif choice == "2":
        uninstall()
    elif choice == "3":
        status()
    else:
        print("Invalid choice")


if __name__ == "__main__":
    main()