"""Business tier — turns stored brand tokens into a deliverable theme (CSS / JSON).

This is what makes the platform white-labelable: the admin console and any tenant-facing
surface fetch /branding/{tenant_id}/theme.css and apply it at runtime, so the same code
renders under each bank's identity.
"""
from .models import Branding


def theme_tokens(b: Branding) -> dict:
    return {
        "displayName": b.display_name,
        "logoUrl": b.logo_url,
        "defaultTheme": b.default_theme,
        "colors": {
            "primary": b.primary_color,
            "accent": b.accent_color,
            "neutral": b.neutral_color,
        },
    }


def theme_css(b: Branding) -> str:
    """Emit CSS custom properties consumed by the front-end."""
    return (
        ":root{\n"
        f"  --brand-primary: {b.primary_color};\n"
        f"  --brand-accent: {b.accent_color};\n"
        f"  --brand-neutral: {b.neutral_color};\n"
        f"  --brand-name: \"{b.display_name}\";\n"
        f"  --brand-default-theme: {b.default_theme};\n"
        "}\n"
    )
