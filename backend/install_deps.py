import subprocess
import sys
import urllib.request

REQUIREMENTS_URL = "https://raw.githubusercontent.com/zyddnys/manga-image-translator/main/requirements.txt"

def main():
    print("===================================================")
    print("Downloading manga-image-translator requirements...")
    try:
        # Some users might have githubusercontent blocked, so we use a shorter timeout
        req = urllib.request.urlopen(REQUIREMENTS_URL, timeout=10)
        content = req.read().decode('utf-8')
    except Exception as e:
        print(f"Failed to download requirements from GitHub: {e}")
        print("Falling back to core dependencies... (Network issue detected)")
        # Fallback to the absolute minimum needed to avoid cv2 missing error
        content = """
networkx
scikit-image
opencv-python
cryptography
aiohttp
tqdm
einops
numpy<2.0.0
requests
"""

    lines = content.splitlines()
    packages = []
    for line in lines:
        line = line.split('#')[0].strip() # Remove comments
        if not line:
            continue

        # pydensecrf ALWAYS fails on Windows without C++ Build Tools
        # If it fails in a normal pip install -r, it aborts EVERYTHING!
        if 'pydensecrf' in line.lower():
            print(f"Skipping problematic package: {line} (Prevents C++ build errors)")
            continue

        packages.append(line)

    print(f"Found {len(packages)} packages to install.")
    print("Installing packages one by one so a single failure won't stop the rest...")

    # We explicitly install numpy first, then opencv, as they are core to image processing
    core_packages = ['numpy<2.0.0', 'opencv-python', 'scikit-image']
    for pkg in core_packages:
        print(f"--> Installing core package: {pkg}")
        subprocess.run([sys.executable, "-m", "pip", "install", pkg])

    # Now install the rest, catching any individual failures
    for pkg in packages:
        # Skip if already installed above
        if any(pkg.startswith(core.split('<')[0]) for core in core_packages):
            continue

        print(f"--> Installing: {pkg}")
        subprocess.run([sys.executable, "-m", "pip", "install", pkg])

    print("===================================================")
    print("Dependency installation completed.")

if __name__ == "__main__":
    main()
