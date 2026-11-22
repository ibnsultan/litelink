# LiteLink OS - run on a Debian 13 build host as root (sudo make iso).
BUILD   := build
OUT     := out
ISO_SRC := $(BUILD)/live-image-amd64.hybrid.iso
VERSION ?= $(shell date +%Y%m%d)
ISO     := $(OUT)/litelink-$(VERSION)-amd64.iso

SHELL_DST := $(BUILD)/config/includes.chroot/usr/lib/litelink/shell

.PHONY: iso branding shell clean distclean sizes deps

# Build-host packages (Debian 13)
deps:
	apt-get install -y live-build imagemagick grub-common fonts-inter squashfs-tools

# Render boot art (Plymouth, GRUB, isolinux) from branding/ into build/config/
branding:
	sh branding/render.sh

# Copy the desktop shell (shell/) into the image
shell:
	rm -rf $(SHELL_DST)
	mkdir -p $(SHELL_DST)
	cp -r shell/main.py shell/theme.css shell/litelink $(SHELL_DST)/
	find $(SHELL_DST) -name __pycache__ -prune -exec rm -rf {} +
	# Builds run as root; keep the folder owned like the rest of the checkout so
	# syncing the repo as a normal user (tools/vm-build.ps1) can update it
	chown -R --reference=$(BUILD)/config $(dir $(SHELL_DST))

iso: branding shell
	chmod +x $(BUILD)/auto/* $(BUILD)/config/hooks/normal/*.hook.chroot $(BUILD)/config/hooks/normal/*.hook.binary \
		$(BUILD)/config/includes.chroot/usr/local/bin/* $(BUILD)/config/includes.chroot/usr/local/sbin/* \
		$(SHELL_DST)/main.py
	# Scripts edited on Windows may carry CRLF line ends: "#!/usr/bin/python3\r" can't be run
	find $(BUILD)/auto $(BUILD)/config/hooks/normal $(BUILD)/config/includes.chroot/usr/local/bin \
		$(BUILD)/config/includes.chroot/usr/local/sbin $(SHELL_DST)/main.py -maxdepth 1 -type f \
		-exec sed -i 's/\r$$//' {} +
	cd $(BUILD) && lb clean && lb config && lb build
	mkdir -p $(OUT)
	cp $(ISO_SRC) $(ISO)
	$(MAKE) --no-print-directory sizes

sizes:
	@echo "ISO:     $$(du -h $(ISO_SRC) | cut -f1)"
	@echo "rootfs:  $$(du -sh --exclude=proc --exclude=sys --exclude=dev $(BUILD)/chroot 2>/dev/null | cut -f1)"
	@echo "squash:  $$(du -h $(BUILD)/binary/live/filesystem.squashfs | cut -f1)"
	@[ -e $(BUILD)/binary/live/web.squashfs ] && echo "web:     $$(du -h $(BUILD)/binary/live/web.squashfs | cut -f1) (module, installed with Web kiosk)" || true
	@[ -e $(BUILD)/binary/live/print.squashfs ] && echo "print:   $$(du -h $(BUILD)/binary/live/print.squashfs | cut -f1) (module, installed with Printers)" || true

clean:
	cd $(BUILD) && lb clean

distclean: clean
	cd $(BUILD) && lb clean --purge
	rm -rf $(OUT)
