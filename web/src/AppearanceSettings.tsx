import { useId, useState, type CSSProperties } from "react";

export const animeThemes: Record<
  string,
  {
    name: string;
    description: string;
    accent: string;
    background: string;
    surface: string;
  }
> = {
  "one-piece": {
    name: "One Piece",
    description: "Ocean blue, treasure gold & a compass rose.",
    accent: "#f4c56a",
    background: "#0b1623",
    surface: "#183349",
  },
  "black-clover": {
    name: "Black Clover",
    description: "Grimoire black, emerald magic & aged gold.",
    accent: "#91e0ac",
    background: "#0b1412",
    surface: "#1a3028",
  },
  "highschool-dxd": {
    name: "High School DxD",
    description: "Crimson, midnight violet & arcane silver.",
    accent: "#ef9fbe",
    background: "#180e19",
    surface: "#391d30",
  },
};

export function AppearanceSettings({
  theme,
  accent,
}: {
  theme: string;
  accent: string;
}) {
  const [selected, setSelected] = useState(theme);
  const id = useId();
  return (
    <>
      <fieldset className="theme-picker">
        <legend>Theme</legend>
        <div className="standard-themes">
          {[
            ["system", "System"],
            ["dark", "Dark"],
            ["light", "Light"],
          ].map(([value, label]) => (
            <label key={value}>
              <input
                type="radio"
                name="theme"
                value={value}
                checked={selected === value}
                onChange={() => setSelected(value)}
              />
              {label}
            </label>
          ))}
        </div>
        <h3>Anime worlds</h3>
        <p className="muted">
          Choose a palette for your library. Changes apply when you save.
        </p>
        <div className="anime-themes">
          {Object.entries(animeThemes).map(([value, preset]) => (
            <label className="theme-choice" key={value}>
              <input
                type="radio"
                name="theme"
                value={value}
                checked={selected === value}
                onChange={() => setSelected(value)}
              />
              <span
                className="theme-preview"
                aria-hidden="true"
                style={
                  {
                    "--preview-bg": preset.background,
                    "--preview-surface": preset.surface,
                    "--preview-accent": preset.accent,
                  } as CSSProperties
                }
              >
                <span className="preview-rail">
                  <i />
                  <i />
                  <i />
                </span>
                <span className="preview-lines">
                  <i />
                  <i />
                  <i />
                </span>
                <img src={`/themes/${value}.svg`} alt="" />
              </span>
              <strong>{preset.name}</strong>
              <small>{preset.description}</small>
            </label>
          ))}
        </div>
        <small>
          Theme colors apply across your library, rules and cleanup queue.
        </small>
      </fieldset>
      <div className="field">
        <label htmlFor={id}>Accent color</label>
        <input
          id={id}
          type="color"
          name="accent"
          defaultValue={accent}
          aria-describedby={`${id}-help`}
        />
        <small id={`${id}-help`}>
          Your custom accent applies to System, Dark and Light. Anime themes use
          their own signature colors.
        </small>
      </div>
    </>
  );
}
