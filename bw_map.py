"""Render a street-only map image using OpenStreetMap data (via OSMnx).

This script is optimized for performance: it renders only street edges (no
water/parks/buildings layers), and it can aggressively clear the OSMnx cache
folder between runs.

Usage:
- Interactive: run with no args and enter a place query.
- CLI: pass a place query as arguments, e.g. `python bw_map.py "Assam, India"`.

Environment variables:
- PYMAP_BG: background hex color (e.g. #ffffff, #f8eac2)
- PYMAP_KEEP_CACHE=1: keep cache folder between runs
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# Lazy import to provide a friendly error if dependencies are missing
try:
    import osmnx as ox
    import matplotlib.pyplot as plt
except Exception as e:
    print("Required packages not found. Please install dependencies first: osmnx, matplotlib, geopandas")
    print("Tip: pip install -r requirements.txt")
    sys.exit(1)


DEFAULT_BG_COLOR = "#f8eac2"  # light cream background
DEFAULT_EDGE_COLOR = "#111111"
DEFAULT_EDGE_LINEWIDTH = 0.6
LARGE_AREA_THRESHOLD_SQM = 2_000_000_000  # ~2,000 km^2


def _normalize_hex_color(color: str) -> Optional[str]:
    """Return a normalized #rgb/#rrggbb string, or None if invalid."""
    if not isinstance(color, str):
        return None
    value = color.strip()
    if not value.startswith("#"):
        return None
    if len(value) not in (4, 7):
        return None
    hex_part = value[1:]
    if not all(ch in "0123456789abcdefABCDEF" for ch in hex_part):
        return None
    return value


def render_detailed_map(place_query: str, output_path: Path) -> Path:
    """
    Render a street map image for the given place with an emphasis on
    streets only for faster generation. Non-street layers like water,
    parks/greens, and buildings are intentionally omitted to improve
    performance and reduce memory usage without changing the street
    map format.

    Parameters
    - place_query: Free-form place string, e.g., "Kolkata, West Bengal, India".
    - output_path: Path to save the PNG image.

    Returns
    - Path to the generated image.
    """
    # Configure OSMnx for consistent style
    ox.settings.use_cache = True
    ox.settings.log_console = False
    # Use local workspace cache folder so we can clean it
    ox.settings.cache_folder = str(Path("cache").resolve())
    # Be conservative with rate-limiting to reduce failed requests
    ox.settings.overpass_rate_limit = True

    # Colors and style
    # Default background is LIGHT CREAM. You can override via env var:
    # - PYMAP_BG=#ffffff (any hex color)
    bg_env = os.getenv("PYMAP_BG", "")
    bgcolor = _normalize_hex_color(bg_env) or DEFAULT_BG_COLOR
    edge_color = DEFAULT_EDGE_COLOR  # street lines
    edge_linewidth = DEFAULT_EDGE_LINEWIDTH
    # Non-street layers removed for performance

    # Boundary polygon for clipping
    boundary = ox.geocode_to_gdf(place_query)
    boundary_proj = ox.projection.project_gdf(boundary)
    # Area-based optimization: for very large areas (e.g., states),
    # restrict to major roads to speed up queries and rendering.
    area_m2 = float(boundary_proj.area.sum())

    # Street network selection
    # For large areas (states), limit to a curated set of road classes and increase
    # max query area size to reduce the number of sub-queries (faster).
    # For smaller areas, include service roads for extra detail.
    original_max_query_area_size = getattr(ox.settings, "max_query_area_size", None)
    if area_m2 >= LARGE_AREA_THRESHOLD_SQM:
        major_roads_filter = (
            '["highway"~"motorway|trunk|primary|secondary|tertiary|'
            'motorway_link|trunk_link|primary_link|secondary_link|tertiary_link|'
            'residential|unclassified|living_street"]'
        )
        try:
            if original_max_query_area_size is not None:
                ox.settings.max_query_area_size = min(area_m2, float(original_max_query_area_size) * 10.0)
            G = ox.graph_from_place(
                place_query,
                custom_filter=major_roads_filter,
                simplify=True,
                retain_all=False,
            )
        except Exception:
            G = ox.graph_from_place(place_query, network_type="drive", simplify=True)
    else:
        # More detailed than "drive" (includes service roads)
        try:
            G = ox.graph_from_place(place_query, network_type="drive_service", simplify=True)
        except Exception:
            G = ox.graph_from_place(place_query, network_type="drive", simplify=True)

    if original_max_query_area_size is not None:
        ox.settings.max_query_area_size = original_max_query_area_size

    # Plot streets using OSMnx's LineCollection-based plotting (faster and lighter
    # than converting to GeoDataFrames and plotting with GeoPandas).
    fig, ax = ox.plot_graph(
        G,
        figsize=(12, 8),
        bgcolor=bgcolor,
        node_size=0,
        edge_color=edge_color,
        edge_linewidth=edge_linewidth,
        show=False,
        close=False,
    )
    # Ensure background is actually baked into the exported PNG.
    # Some Matplotlib styles/viewers can otherwise show a dark background.
    fig.set_facecolor(bgcolor)
    ax.set_facecolor(bgcolor)
    fig.patch.set_alpha(1.0)
    ax.patch.set_alpha(1.0)
    ax.axis("off")

    # Bottom-right location label (similar to reference image)
    display_name = place_query.split(",")[0].strip() or place_query.strip()
    # Creation month (local time)
    created_date = datetime.now().strftime("%B")
    fig.text(
        0.98,
        0.03,
        display_name,
        ha="right",
        va="bottom",
        color="#111111",
        fontsize=17,
    )
    # Optional small data credit
    fig.text(
        0.98,
        0.01,
        f"{created_date} @Voidnova",
        ha="right",
        va="bottom",
        color="#111111",
        fontsize=10,
    )

    # Save output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_path,
        dpi=300,
        bbox_inches="tight",
        facecolor=bgcolor,
        transparent=False,
    )
    plt.close(fig)

    # OSMnx/Matplotlib can still save an RGBA PNG with transparent pixels.
    # Flatten onto a solid background so viewers never show black behind it.
    try:
        flatten_png_background(output_path, bgcolor)
    except Exception:
        pass
    # Clear cache after every run to avoid disk growth.
    # Set env var PYMAP_KEEP_CACHE=1 to keep cache for faster repeated runs.
    if os.getenv("PYMAP_KEEP_CACHE", "").strip() != "1":
        try:
            clean_cache_folder(Path(ox.settings.cache_folder))
        except Exception:
            pass
    return output_path


def main():
    # Optional CLI: pass the place name as argument(s)
    place = " ".join(sys.argv[1:]).strip()
    if not place:
        print("Enter a city/state/country (e.g.,'Guwahati/Assam/India'):")
        place = input().strip()
    if not place:
        print("No input provided. Exiting.")
        sys.exit(1)

    out = Path("map_detailed.png").resolve()
    try:
        path = render_detailed_map(place, out)
        print(f"Saved detailed map to: {path}")
    except Exception as e:
        print("Failed to render map. Try a more specific place name.")
        print(f"Error: {e}")
        sys.exit(2)


def clean_cache_folder(folder: Path) -> None:
    """Remove all files/subfolders inside the given cache folder, leaving the folder itself."""
    if not folder.exists() or not folder.is_dir():
        return
    for child in folder.iterdir():
        try:
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                import shutil
                shutil.rmtree(child, ignore_errors=True)
        except Exception:
            pass


def flatten_png_background(image_path: Path, bgcolor: str) -> None:
    """Convert RGBA PNG to RGB by compositing onto a solid background."""
    try:
        from PIL import Image
    except Exception:
        # Optional dependency: if Pillow isn't installed, skip flattening.
        return

    rgb = (255, 255, 255)
    normalized = _normalize_hex_color(bgcolor)
    if normalized:
        hex_color = normalized[1:]
        if len(hex_color) == 3:
            r, g, b = (int(ch * 2, 16) for ch in hex_color)
            rgb = (r, g, b)
        elif len(hex_color) == 6:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            rgb = (r, g, b)

    img = Image.open(image_path)
    if "A" not in img.getbands():
        return

    img_rgba = img.convert("RGBA")
    bg = Image.new("RGBA", img_rgba.size, (rgb[0], rgb[1], rgb[2], 255))
    out = Image.alpha_composite(bg, img_rgba).convert("RGB")
    out.save(image_path, format="PNG")


if __name__ == "__main__":
    main()
