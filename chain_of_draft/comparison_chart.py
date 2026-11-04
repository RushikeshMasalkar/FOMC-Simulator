import matplotlib.pyplot as plt

labels = ["Actual FOMC Decision", "AI Predicted Decision"]
values = [-0.50, -0.25]

plt.figure(figsize=(8, 5))
bars = plt.bar(labels, values)

plt.axhline(0, linewidth=0.8)
plt.ylabel("Interest Rate Change (%)")
plt.title("September 2024 FOMC: Actual vs Predicted Rate Change")

for bar, value in zip(bars, values):
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        value - 0.02,
        f"{value}%",
        ha="center"
    )

plt.tight_layout()
plt.savefig("comparison_chart.png", dpi=300)
plt.show()