/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Brand Colors — modern indigo/slate enterprise palette
        sas: {
          blue: {
            50:  '#eef2ff', // indigo-50
            100: '#e0e7ff', // indigo-100
            200: '#c7d2fe', // indigo-200
            300: '#a5b4fc', // indigo-300
            400: '#818cf8', // indigo-400
            500: '#6366f1', // indigo-500  ← primary accent
            600: '#4f46e5', // indigo-600
            700: '#4338ca', // indigo-700
            800: '#3730a3', // indigo-800
            900: '#312e81', // indigo-900
          },
          red: {
            50:  '#fff1f0',
            100: '#ffe3e0',
            200: '#ffc7c2',
            300: '#ffaba3',
            400: '#ff8f85',
            500: '#ef4444', // modern red
            600: '#dc2626',
            700: '#b91c1c',
            800: '#991b1b',
            900: '#7f1d1d',
          },
          gray: {
            50:  '#f8fafc', // slate-50
            100: '#f1f5f9', // slate-100
            200: '#e2e8f0', // slate-200
            300: '#cbd5e1', // slate-300
            400: '#94a3b8', // slate-400
            500: '#64748b', // slate-500
            600: '#475569', // slate-600
            700: '#334155', // slate-700
            800: '#1e293b', // slate-800
            900: '#0f172a', // slate-900
          },
        },
        // Keep azure for backwards compatibility
        azure: {
          50:  '#f0f9ff',
          100: '#e0f2fe',
          200: '#bae6fd',
          300: '#7dd3fc',
          400: '#38bdf8',
          500: '#0ea5e9',
          600: '#0284c7',
          700: '#0369a1',
          800: '#075985',
          900: '#0c4a6e',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),
    require('@tailwindcss/typography'),
  ],
}