#!/usr/bin/env bash
# A TELEMAC setup script that exports the real sentinel (SENTINELS["telemac"]) and puts
# the fake launcher on PATH, so SolverEnvironment.capture()/validate() succeed in CI -
# a path that has never had end-to-end coverage.
_FAKE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# A build folder of empty files, laid out like TELEMAC v9's (CMake), so the inspection of
# the installation (axqua.core.installation) finds TELEMAC-2D, -3D, GAIA, partel and the
# compiler named in build_commands.json. FAKE_TELEMAC_BUILD names another one.
export HOMETEL="${HOMETEL_FAKE:-$_FAKE_ROOT/telemac}"
export SYSTELCFG="$HOMETEL/configs/systel.cfg"
export BUILD_DIR="${FAKE_TELEMAC_BUILD:-$HOMETEL/builds/fake}"
export PATH="$_FAKE_ROOT/bin:$BUILD_DIR/bin:$PATH"
# TelemacRuntime.check_available proves the environment by importing TELEMAC's own
# SELAFIN reader in it, so the stand-in package has to be importable too.
export PYTHONPATH="$_FAKE_ROOT/pylib${PYTHONPATH:+:$PYTHONPATH}"
echo "FAKE TELEMAC environment set: $HOMETEL"
