/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#0b1220",
          900: "#0f1a2a",
          800: "#162338",
          700: "#1e2f4a",
        },
        accent: {
          500: "#0f9d8a",
          600: "#0d8a79",
          700: "#0b7365",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "Segoe UI", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(15, 26, 42, 0.06), 0 8px 24px rgba(15, 26, 42, 0.06)",
      },
    },
  },
  plugins: [],
};
