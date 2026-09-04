/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['Cascadia Mono', 'Consolas', 'monospace'],
        sans: ['Segoe UI', 'Arial', 'sans-serif'],
      },
      boxShadow: {
        nb: '4px 4px 0 0 rgba(0,0,0,1)',
        nbsm: '2px 2px 0 0 rgba(0,0,0,1)',
        nblg: '6px 6px 0 0 rgba(0,0,0,1)',
        nbdark: '4px 4px 0 0 rgba(255,255,255,0.9)',
        nbdarksm: '2px 2px 0 0 rgba(255,255,255,0.9)',
        nbdarklg: '6px 6px 0 0 rgba(255,255,255,0.9)',
      },
      colors: {
        brand: {
          pri: '#FFEB3B',
          ok: '#7BFFB5',
          warn: '#FFD166',
          err: '#FF5C7C',
          info: '#7FB7FF',
          violet: '#C4A9FF',
        },
      },
    },
  },
  plugins: [],
}
