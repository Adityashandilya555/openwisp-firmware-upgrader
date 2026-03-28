"""
Management command: create_demo_data

Sets up a complete demo environment to exercise the Persistent &
Scheduled Firmware Upgrade prototype in the Django admin.

What it creates
---------------
- 1 Organization  ("Demo Org")
- 1 Category      ("Demo Routers")
- 2 Builds        (v1.0  ← currently installed,  v2.0  ← upgrade target)
- 2 FirmwareImages per build  (TP-Link WDR4300 v1 + IL variant)
- 1 SSH Credentials object    (username/password, no real server needed)
- 5 Devices with Config + DeviceConnection  (models matching the images)
- 5 DeviceFirmware records    (pointing to v1.0 images, installed=True)

After running this command
--------------------------
1. Go to Admin → Firmware Upgrader → Builds
2. Click "v2.0 (Demo Routers)"
3. Select action "Upgrade related devices" → Confirm
4. You will see the new confirmation page with:
   - Persistent checkbox (checked by default)
   - Scheduled datetime picker
"""

import os

import swapper
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from openwisp_firmware_upgrader.swapper import load_model

Build = load_model("Build")
Category = load_model("Category")
DeviceFirmware = load_model("DeviceFirmware")
FirmwareImage = load_model("FirmwareImage")

Device = swapper.load_model("config", "Device")
Config = swapper.load_model("config", "Config")
Credentials = swapper.load_model("connection", "Credentials")
DeviceConnection = swapper.load_model("connection", "DeviceConnection")
Organization = swapper.load_model("openwisp_users", "Organization")

User = get_user_model()

# Two image types whose boards are in hardware.py
IMAGE_TYPE_A = "ath79-generic-tplink_tl-wdr4300-v1-squashfs-sysupgrade.bin"
IMAGE_TYPE_B = "ath79-generic-tplink_tl-wdr4300-v1-il-squashfs-sysupgrade.bin"
BOARD_A = "TP-Link TL-WDR4300 v1"
BOARD_B = "TP-LINK TL-WDR4300 v1 (IL)"

# Must match openwisp_controller.connection.settings.CONNECTORS[0][0]
# and CONFIG_UPDATE_MAPPING["netjsonconfig.OpenWrt"]
SSH_CONNECTOR = "openwisp_controller.connection.connectors.openwrt.ssh.OpenWrt"


class Command(BaseCommand):
    help = "Create demo data for the Persistent & Scheduled Upgrades prototype"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing demo data before recreating it",
        )

    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        org = self._get_or_create_org()
        self.stdout.write(f"  Organization : {org.name}")

        admin = self._ensure_superuser()
        self.stdout.write(
            f"  Superuser    : {admin.username} / admin (if newly created)"
        )

        category = self._get_or_create_category(org)
        self.stdout.write(f"  Category     : {category.name}")

        build_old, build_new = self._get_or_create_builds(category)
        self.stdout.write(f"  Build v1.0   : pk={build_old.pk}")
        self.stdout.write(f"  Build v2.0   : pk={build_new.pk}")

        img_old_a, img_old_b = self._get_or_create_images(build_old)
        img_new_a, img_new_b = self._get_or_create_images(build_new)
        self.stdout.write("  FirmwareImages: created/verified for both builds")

        creds = self._get_or_create_credentials(org)
        self.stdout.write(f"  Credentials  : {creds.name}")

        devices = self._get_or_create_devices(org, creds, img_old_a, img_old_b)
        self.stdout.write(f"  Devices      : {len(devices)} created/verified")

        self.stdout.write(self.style.SUCCESS("\nDemo data ready."))
        self.stdout.write(
            "\nNext steps:\n"
            "  1. python manage.py runserver\n"
            "  2. Go to /admin/  (login: admin / admin)\n"
            "  3. Firmware Upgrader → Builds → click 'v2.0 (Demo Routers)'\n"
            "  4. Choose action 'Upgrade related devices' → Go\n"
            "  5. You will see the new confirmation page with persistent checkbox\n"
            "     and scheduled datetime picker.\n"
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _flush(self):
        self.stdout.write("Flushing existing demo data...")
        DeviceFirmware.objects.filter(
            image__build__category__name="Demo Routers"
        ).delete()
        Build.objects.filter(category__name="Demo Routers").delete()
        Category.objects.filter(name="Demo Routers").delete()
        Device.objects.filter(name__startswith="demo-device-").delete()
        Credentials.objects.filter(name="Demo SSH Credentials").delete()
        Organization.objects.filter(name="Demo Org").delete()

    def _get_or_create_org(self):
        org, _ = Organization.objects.get_or_create(
            name="Demo Org",
            defaults={"slug": "demo-org"},
        )
        return org

    def _ensure_superuser(self):
        if User.objects.filter(is_superuser=True).exists():
            return User.objects.filter(is_superuser=True).first()
        user = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="admin",
        )
        return user

    def _get_or_create_category(self, org):
        cat, _ = Category.objects.get_or_create(
            name="Demo Routers",
            organization=org,
        )
        return cat

    def _get_or_create_builds(self, category):
        build_old, _ = Build.objects.get_or_create(
            category=category,
            version="1.0",
        )
        build_new, _ = Build.objects.get_or_create(
            category=category,
            version="2.0",
        )
        return build_old, build_new

    def _make_fake_firmware_file(self, name):
        """Return a ContentFile with fake binary content."""
        content = os.urandom(512)
        return ContentFile(content, name=name)

    def _get_or_create_images(self, build):
        img_a, created_a = FirmwareImage.objects.get_or_create(
            build=build,
            type=IMAGE_TYPE_A,
            defaults={
                "file": self._make_fake_firmware_file(
                    f"openwrt-{build.version}-{IMAGE_TYPE_A}"
                )
            },
        )
        img_b, created_b = FirmwareImage.objects.get_or_create(
            build=build,
            type=IMAGE_TYPE_B,
            defaults={
                "file": self._make_fake_firmware_file(
                    f"openwrt-{build.version}-{IMAGE_TYPE_B}"
                )
            },
        )
        return img_a, img_b

    def _get_or_create_credentials(self, org):
        creds, _ = Credentials.objects.get_or_create(
            name="Demo SSH Credentials",
            defaults={
                "connector": SSH_CONNECTOR,
                "params": {"username": "root", "password": "password", "port": 22},
                "organization": org,
            },
        )
        return creds

    def _get_or_create_devices(self, org, creds, img_old_a, img_old_b):
        # 3 devices with board A, 2 with board B
        device_specs = [
            ("demo-device-01", "00:11:22:33:44:01", BOARD_A, img_old_a),
            ("demo-device-02", "00:11:22:33:44:02", BOARD_A, img_old_a),
            ("demo-device-03", "00:11:22:33:44:03", BOARD_A, img_old_a),
            ("demo-device-04", "00:11:22:33:44:04", BOARD_B, img_old_b),
            ("demo-device-05", "00:11:22:33:44:05", BOARD_B, img_old_b),
        ]
        devices = []
        for name, mac, board, old_image in device_specs:
            device = self._get_or_create_device(name, mac, board, org)
            self._ensure_config(device)
            self._ensure_device_connection(device, creds)
            self._ensure_device_firmware(device, old_image)
            devices.append(device)
        return devices

    def _get_or_create_device(self, name, mac, model, org):
        device, _ = Device.objects.get_or_create(
            name=name,
            defaults={
                "mac_address": mac,
                "model": model,
                "organization": org,
            },
        )
        return device

    def _ensure_config(self, device):
        if not Config.objects.filter(device=device).exists():
            Config.objects.create(
                device=device,
                backend="netjsonconfig.OpenWrt",
                config={"interfaces": []},
            )

    def _ensure_device_connection(self, device, creds):
        if not DeviceConnection.objects.filter(device=device).exists():
            dc = DeviceConnection(
                device=device,
                credentials=creds,
                enabled=True,
                params={},
            )
            # full_clean() auto-sets update_strategy from the device's
            # config backend (netjsonconfig.OpenWrt → OpenWrt SSH)
            dc.full_clean()
            dc.save()

    def _ensure_device_firmware(self, device, image):
        if not DeviceFirmware.objects.filter(device=device).exists():
            df = DeviceFirmware(device=device, image=image)
            df.full_clean()
            # save(upgrade=False) — we don't want to trigger actual upgrade
            df.save(upgrade=False)
            # mark as installed so the next build sees them as "upgradeable"
            DeviceFirmware.objects.filter(pk=df.pk).update(installed=True)
