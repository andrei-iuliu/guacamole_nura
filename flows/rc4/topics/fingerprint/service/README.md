# Service additions: none active

No fingerprint daemon, HAL bridge, supplicant or PAM module is integrated in
this topic.  Gate 3 (QSEECom/QTEE transport), gate 4 (stock HIDL HAL in a
chroot) and gate 6 (shell/PAM integration) have not been attempted, and
adding such a service now would imply authentication that does not exist.

The only unit here is `fingerprint-gate0.service`: a **disabled** template for
a passive gate-0 diagnostic.  It runs `gate0-check.sh` without `--load` and
without `--diagnostics`, so the unit itself does not load `qcomtee`, issue a
secure-world call, power the sensor, touch persist/RPMB or start a HAL.  It
also requires `/etc/guacamole/fingerprint-gate0-enabled`, so it cannot run by
accident.  Note that `QCOMTEE=m` can still be autoloaded by udev/systemd
through the `platform:qcomtee` modalias; keep `qcomtee.ko` uninstalled or use
`module_blacklist=qcomtee` if the whole boot must stay passive.

If the parent decides to install it:

1. install `scripts/gate0-check.sh` at
   `/usr/libexec/guacamole/fingerprint/gate0-check.sh` (or change `ExecStart`);
2. install the unit, then `systemctl daemon-reload`;
3. leave it disabled; enable it only for a deliberate diagnostic, then disable
   it again.

Do not add a service that runs `modprobe qcomtee`, `smcinvoke_client`, a
fingerprint HAL, or anything that writes `/proc/fp_id`, `/proc/tee_bind_core`,
`persist` or RPMB as part of boot.
