# rc4 black-screen / reboot: preliminary source review

User reports a black screen after apparently reaching Plasma (power-button sound), followed by reboot to fastboot. No crash log captured yet.

Compared actual rc4 kernel source with the prepared rc3 kernel: panel-samsung-oneplus-dsc.c, dpu_kms.c, adreno/a6xx_gpu.c, s6sy761.c and fastrpc.c are byte-identical. This rules out direct rc4 code edits in these files, not pre-existing defects or different runtime behavior.

New default runtime paths include enabled DisplayPort controller/FSA4480 and Hall sensors/LPG. Fingerprint and popup motor DT are disabled, front sensor/CAMSS endpoint absent, QCOMTEE kernel-blacklisted, MSS rail release service disabled. DP audio link is disabled.

DP enters DPU modeset initialization in dpu_kms.c and has runtime PM callbacks in dp/dp_display.c:1447 onward; enabling it changes display/PHY dependencies even with unchanged panel code. No causal defect established.

Earlier rc3 graphics-context report documented compositor/shell lost-context behavior; earlier rc1/rc2 logs contained GPU faults. That is historical evidence, not evidence from this crash. System suspend is disabled by policy; display blanking can still use runtime PM.

Next evidence: debug-shell pstore and previous persistent journal. If inconclusive, test an isolated DP-disabled boot variant while preserving identical kernel, rootfs and other DT changes. Do not change several subsystems at once or label either hypothesis confirmed.
