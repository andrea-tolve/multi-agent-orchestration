import pandas as pd
import matplotlib.pyplot as plt

# Carica il CSV
df = pd.read_csv("info.csv")

# Considera solo valori validi
df = df[df["success_rate (%)"] >= 0]

# Calcola la media del success rate per tipo
success_rate = df.groupby("type")["success_rate (%)"].mean()

# Plot
plt.figure(figsize=(6, 5))

plt.bar(
    success_rate.index,
    success_rate.values
)

# Etichette valori sopra le colonne
for i, value in enumerate(success_rate.values):
    plt.text(
        i,
        value + 2,
        f"{value:.1f}%",
        ha="center",
        fontsize=12
    )

plt.ylabel("Average Success Rate (%)")
plt.title("Success Rate Comparison")

plt.ylim(0, 100)
plt.grid(axis="y", linestyle="--", alpha=0.5)

plt.tight_layout()

plt.savefig("success_rate_comparison.png", dpi=300)
plt.show()
