install_prefix="$HOME/.local/openfoam-sediment-v2406"
if [ -d "$install_prefix" ] && [ ! -L "$install_prefix" ] \
   && [ -f "$install_prefix/.sediment-installer.json" ]; then
    recovery_dir="$(mktemp -d "${install_prefix}.failed-XXXXXXXX")" \
      && mv -T -- "$install_prefix" "$recovery_dir/install" \
      && printf 'Preserved at: %s\n' "$recovery_dir/install"
fi

