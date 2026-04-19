# Compound Interest Calculator Chrome Extension

## Overview
A sleek, glassmorphic Chrome extension that helps users calculate compound interest on the fly. The extension provides a clean UI, supports custom principal, rate, periods, and contributions, and instantly shows yearly breakdowns. It is built with modern web technologies and packaged as a Chrome extension.

---

## Features
- **Glassmorphic UI**: Modern, frosted‑glass style with smooth animations.
- **Full‑featured calculator**:
  - Principal, annual interest rate, years, compounding frequency.
  - Optional periodic contributions.
  - Year‑by‑year breakdown table.
- **Responsive design** – works on any screen size.
- **Local storage** – remembers the last calculation.
- **Zero permissions** – only uses `storage` permission.

---

## Installation
1. **Download the source** (or clone the repository):
   ```bash
   git clone https://github.com/yourusername/compound-interest-calculator.git
   cd compound-interest-calculator/chrome-extension
   ```
2. Open Chrome and navigate to `chrome://extensions/`.
3. Enable **Developer mode** (toggle in the top‑right corner).
4. Click **Load unpacked** and select the `chrome-extension` folder.
5. The extension icon will appear next to the address bar.

---

## Usage
1. Click the extension icon → the popup appears.
2. Fill in the fields:
   - **Principal** – initial amount (e.g., `1000`).
   - **Rate (%)** – annual interest rate (e.g., `5`).
   - **Years** – number of years to compound.
   - **Compounding Frequency** – `Annually`, `Semi‑annually`, `Quarterly`, `Monthly`.
   - **Contribution (optional)** – amount added each period.
3. Press **Calculate**.
4. The result area shows the final amount and a table with the balance after each year.

---

## Development
The extension consists of three core files:
- `manifest.json` – defines the extension, permissions, and UI.
- `popup.html` – the markup for the popup window.
- `popup.js` – JavaScript logic for the calculator.
- `styles.css` – glassmorphic styling.

### Running locally
The UI updates instantly as you edit files. Open `popup.html` directly in the browser for quick iteration, then reload the extension via `chrome://extensions/`.

### Building a production bundle
If you want a zipped package for distribution:
```bash
cd chrome-extension
zip -r ../compound-interest-calculator.zip *
```
Upload the zip file to the Chrome Web Store developer console.

---

## Contributing
Contributions are welcome! Feel free to:
- Report bugs via the Issues tab.
- Submit pull requests for UI tweaks, new features, or calculations improvements.
- Enhance the design with additional themes or dark mode.

Please follow the existing code style and update the README if you add major features.

---

## License
This project is licensed under the **MIT License** – see the `LICENSE` file for details.

---

## Acknowledgements
- Inspired by classic financial calculators.
- UI concepts based on modern glassmorphism trends.

---

*Happy calculating!*
