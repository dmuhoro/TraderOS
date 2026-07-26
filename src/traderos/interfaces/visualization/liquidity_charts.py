import os

import matplotlib.pyplot as plt
import pandas as pd


class LiquidityVisualizer:
    def __init__(self, output_dir: str = "exports"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def plot_liquidity_map(self, df: pd.DataFrame, zones: list, sweeps: list, symbol: str):
        plt.figure(figsize=(14, 8))
        plt.plot(df["timestamp"], df["close"], label="Price", color="black", alpha=0.6)

        # Plot Support/Resistance Zones
        for zone in zones:
            color = "green" if zone["zone_type"] == "Support" else "red"
            plt.axhline(
                y=zone["price_level"],
                color=color,
                linestyle="--",
                alpha=0.3,
                label=f"{zone['zone_type']} (Str: {zone['strength']})",
            )

        # Plot Liquidity Sweeps
        for sweep in sweeps:
            plt.scatter(
                sweep["timestamp"],
                df.loc[df["timestamp"] == sweep["timestamp"], "close"],
                marker="x",
                color="purple",
                s=100,
                label="Liquidity Sweep",
            )

        # Remove duplicate labels
        handles, labels = plt.gca().get_legend_handles_labels()
        by_label = dict(zip(labels, handles, strict=False))
        plt.legend(by_label.values(), by_label.keys(), loc="best")

        plt.title(f"{symbol} Liquidity Map & Market Structure")
        plt.grid(True, alpha=0.2)
        path = os.path.join(self.output_dir, f"{symbol}_liquidity_map.png")
        plt.savefig(path)
        plt.close()
        return path
