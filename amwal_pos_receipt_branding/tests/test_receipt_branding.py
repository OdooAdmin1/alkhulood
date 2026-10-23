from pathlib import Path

from lxml import etree
from PIL import Image

from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged
from odoo.tools.template_inheritance import apply_inheritance_specs

MODULE = 'amwal_pos_receipt_branding'
POS_BUNDLE = 'point_of_sale._assets_pos'

CORE_RECEIPT = 'static/src/app/screens/receipt_screen/receipt/order_receipt.xml'
OUR_RECEIPT = 'static/src/xml/order_receipt.xml'
OUR_SCSS = 'static/src/scss/receipt_branding.scss'
OUR_LOGO = 'static/src/img/amwal_logo.png'

CORE_TEMPLATE = 'point_of_sale.OrderReceipt'
OUR_TEMPLATE = f'{MODULE}.OrderReceiptAmwalFooter'

NAVY = (22, 41, 73)
GOLD = (170, 145, 91)


def _read_template(module, relpath, name):
    path = f'{get_module_path(module)}/{relpath}'
    for node in etree.parse(path).getroot().iter('t'):
        if node.get('t-name') == name:
            return node
    raise AssertionError(f'template {name} not found in {path}')


@tagged('post_install', '-at_install')
class TestPosReceiptBranding(TransactionCase):
    """The receipt footer must read 'Powered by Amwal W.L.L', not 'Odoo'."""

    def test_xpath_matches_the_upstream_receipt(self):
        """The override resolves against the real point_of_sale template.

        This is the test that matters. The footer is patched by xpath, and an
        xpath that stops matching fails silently in the browser: the receipt
        just keeps saying Odoo. Applying the spec here turns that into a
        failure at upgrade time instead.
        """
        source = _read_template('point_of_sale', CORE_RECEIPT, CORE_TEMPLATE)
        self.assertIn(
            'footer-powered-by',
            etree.tostring(source, encoding='unicode'),
            "point_of_sale no longer marks the receipt footer with "
            "'footer-powered-by'; the override in this module needs a new xpath.",
        )

        override = _read_template(MODULE, OUR_RECEIPT, OUR_TEMPLATE)
        specs = [child for child in override if isinstance(child.tag, str)]
        merged = etree.tostring(apply_inheritance_specs(source, specs), encoding='unicode')

        self.assertIn('Amwal W.L.L', merged)
        self.assertIn('amwal_logo.png', merged)
        self.assertNotIn('Odoo', merged, "the receipt still credits Odoo")

    def test_override_is_loaded_after_the_core_receipt(self):
        """Being in the bundle is not enough: it has to come after the original."""
        paths = [str(p[0]) for p in self.env['ir.asset']._get_asset_paths(POS_BUNDLE, {})]
        core = f'/point_of_sale/{CORE_RECEIPT}'
        ours = f'/{MODULE}/{OUR_RECEIPT}'

        self.assertIn(core, paths)
        self.assertIn(ours, paths, f'{OUR_RECEIPT} is not declared in {POS_BUNDLE}')
        self.assertGreater(
            paths.index(ours),
            paths.index(core),
            'the override is loaded before the template it extends',
        )

    def test_override_reaches_the_compiled_bundle(self):
        bundle = self.env['ir.qweb']._get_asset_bundle(POS_BUNDLE)
        js = bundle.generate_xml_bundle()

        self.assertIn(f'registerTemplateExtension("{CORE_TEMPLATE}"', js)
        self.assertIn('Amwal W.L.L', js)
        self.assertIn('amwal_logo.png', js)

    def test_logo_background_is_transparent(self):
        """The source artwork is a white-matted JPG, which would print a white
        box on a tinted receipt. The shipped asset must carry real alpha."""
        with Image.open(f'{get_module_path(MODULE)}/{OUR_LOGO}') as img:
            self.assertEqual(img.format, 'PNG')
            self.assertEqual(img.mode, 'RGBA', 'logo has no alpha channel')

            w, h = img.size
            for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
                self.assertEqual(
                    img.getpixel(corner)[3],
                    0,
                    f'corner {corner} is opaque, so the logo paints a box',
                )

            alphas = [px[3] for px in img.convert('RGBA').getdata()]
            transparent = sum(1 for a in alphas if a == 0)
            self.assertGreater(
                transparent,
                len(alphas) * 0.4,
                'most of the logo bounding box should be see-through',
            )

    def test_logo_keeps_the_amwal_brand_colours(self):
        with Image.open(f'{get_module_path(MODULE)}/{OUR_LOGO}') as img:
            opaque = {px[:3] for px in img.convert('RGBA').getdata() if px[3] == 255}

        for name, brand in (('navy', NAVY), ('gold', GOLD)):
            self.assertTrue(
                any(sum((c[i] - brand[i]) ** 2 for i in range(3)) < 400 for c in opaque),
                f'the {name} brand colour {brand} is missing from the logo',
            )

    def test_logo_is_light_enough_for_a_receipt(self):
        """Receipts are rendered per order and often printed over a slow link."""
        size = Path(f'{get_module_path(MODULE)}/{OUR_LOGO}').stat().st_size
        self.assertLess(size, 32 * 1024, 'logo is too heavy for a receipt footer')

    def test_logo_is_sized_against_the_text(self):
        """'as small as just above the size of the character' -- so the height
        has to be in em, which tracks the font size, not a fixed pixel box."""
        scss = Path(f'{get_module_path(MODULE)}/{OUR_SCSS}').read_text(encoding='utf-8')
        self.assertRegex(
            scss,
            r'\.amwal-powered-by-logo\s*\{[^}]*height:\s*1(\.\d+)?em',
            'the logo height must be expressed in em, just above the cap height',
        )
