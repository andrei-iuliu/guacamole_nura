# nura-gpu session environment bridge (device-oneplus-guacamole).
#
# Sourced by /etc/profile.  greetd runs the Phosh session through
# `/bin/sh -c '[ -f /etc/profile ] && . /etc/profile; ...; exec phosh-session'`
# because its general.source_profile setting defaults to true (the installed
# /etc/phrog/greetd-config.toml does not override it).  Exporting here, before
# phosh-session is exec'd, means phoc, gnome-session, phosh and every
# application they launch inherit FD_MESA_DEBUG.
#
# The value is written by /usr/bin/nura-gpu-mode.  A missing or invalid state
# file means normal Mesa rendering: this script never unsets FD_MESA_DEBUG, so
# a value the user set elsewhere is preserved.
#
# systemd user services do not inherit this shell.  They read
# /etc/environment.d/90-nura-gpu.conf, written by the same tool.
#
# NURA_GPU_MODE_STATE is a test-only override; the package does not set it.

_nura_gpu_state=${NURA_GPU_MODE_STATE:-/var/lib/nura/gpu-mode}

if [ -r "$_nura_gpu_state" ]; then
	_nura_gpu_mode=$(cat "$_nura_gpu_state" 2>/dev/null || true)
	case "$_nura_gpu_mode" in
		sysmem|inorder)
			FD_MESA_DEBUG=$_nura_gpu_mode
			export FD_MESA_DEBUG
			;;
	esac
	unset _nura_gpu_mode
fi

unset _nura_gpu_state
