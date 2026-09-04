# Maintainer: Vikyek <https://github.com/Vikyek>
pkgname=jules-vanager
pkgver=1.0.0
pkgrel=1
pkgdesc="Google Jules API Manager, Listener Daemon, Interactive TUI, and Conky HUD"
arch=('any')
url="https://github.com/Vikyek/jules-vanager"
license=('GPL-3.0-only')
depends=('python' 'python-requests' 'github-cli' 'systemd')
optdepends=(
    'xclip: Clipboard support for X11'
    'wl-clipboard: Clipboard support for Wayland'
)
source=("$pkgname-$pkgver.tar.gz::$url/archive/refs/tags/v$pkgver.tar.gz")
sha256sums=('SKIP')

package() {
    cd "$pkgname-$pkgver"
    install -Dm755 jules_manager.py "$pkgdir/usr/bin/jules-manager"
    ln -s jules-manager "$pkgdir/usr/bin/jules-vanager"
    install -Dm755 jules_listener.py "$pkgdir/usr/bin/jules-listener"
    install -Dm755 jules_tui.py "$pkgdir/usr/bin/jules-tui"
    install -Dm755 jules_hud.py "$pkgdir/usr/bin/jules-hud"
    install -Dm755 jules_scraper.py "$pkgdir/usr/bin/jules-scraper"
    install -Dm755 jules_cookie_extractor.py "$pkgdir/usr/bin/jules-cookie-extractor"

    install -Dm644 jules-tui.desktop "$pkgdir/usr/share/applications/jules-tui.desktop"
    install -Dm644 jules-hud.desktop "$pkgdir/usr/share/applications/jules-hud.desktop"
    install -Dm644 jules-listener.service "$pkgdir/usr/lib/systemd/user/jules-listener.service"

    install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
