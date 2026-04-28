export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#102542",
        flame: "#f25f5c",
        foam: "#f7f3e9",
        mint: "#70c1b3",
        gold: "#f3b562",
      },
      fontFamily: {
        display: ["Georgia", "serif"],
        body: ["Trebuchet MS", "sans-serif"],
      },
      boxShadow: {
        panel: "0 25px 80px rgba(16, 37, 66, 0.15)",
      },
    },
  },
  plugins: [],
};

