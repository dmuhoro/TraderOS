import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd
import os

class Visualizer:
    def __init__(self, output_dir: str = "exports"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def plot_price_with_regime(self, df: pd.DataFrame, symbol: str):
        plt.figure(figsize=(12, 6))
        plt.plot(df['timestamp'], df['close'], label='Price', color='black', alpha=0.7)
        
        # Color code regimes (simplified)
        if 'regime' in df.columns:
            # This is a placeholder for more complex regime shading
            plt.title(f"{symbol} Price & Regime Analysis")
        
        plt.legend()
        plt.grid(True, alpha=0.3)
        path = os.path.join(self.output_dir, f"{symbol}_analysis.png")
        plt.savefig(path)
        plt.close()
        return path

    def plot_correlation_heatmap(self, corr_matrix: pd.DataFrame):
        plt.figure(figsize=(10, 8))
        sns.heatmap(corr_matrix, annot=True, cmap='coolwarm', center=0)
        plt.title("Market Correlations")
        path = os.path.join(self.output_dir, "correlation_heatmap.png")
        plt.savefig(path)
        plt.close()
        return path
