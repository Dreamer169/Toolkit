{pkgs}: {
  deps = [
    pkgs.libgbm
    pkgs.fontconfig
    pkgs.pango
    pkgs.cairo
    pkgs.alsa-lib
    pkgs.expat
    pkgs.mesa
    pkgs.xorg.libxcb
    pkgs.xorg.libXrandr
    pkgs.xorg.libXfixes
    pkgs.xorg.libXext
    pkgs.xorg.libXdamage
    pkgs.xorg.libXcomposite
    pkgs.xorg.libX11
    pkgs.at-spi2-core
    pkgs.libxkbcommon
    pkgs.dbus
    pkgs.libdrm
    pkgs.cups
    pkgs.at-spi2-atk
    pkgs.atk
    pkgs.nspr
    pkgs.nss
    pkgs.glib
  ];
}
