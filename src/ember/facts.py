"""Cannabis trivia for the app's fact strip."""

from __future__ import annotations

import random

FACTS: tuple[str, ...] = (
    "Cannabis produces over 100 cannabinoids. THC and CBD are just the two everybody knows.",
    "THCA won't get you high. Heat strips off its acid group — that's what your chamber is for.",
    "Decarboxylation starts around 220°F, turning THCA into THC.",
    "Beta-caryophyllene is the only terpene known to bind a cannabinoid receptor directly.",
    "That same caryophyllene is what makes black pepper smell like black pepper.",
    "Myrcene is the most abundant terpene in most modern cannabis.",
    "Limonene turns up in cannabis and citrus peel alike.",
    "Pinene boils at about 311°F — low-temp dabs keep it in the vapor instead of burning it off.",
    "Trichomes are the resin glands doing all the work. Magnified, they look like tiny mushrooms.",
    "Anandamide, found in 1992, is named for 'ananda' — Sanskrit for bliss.",
    "Your body makes its own cannabinoids. The endocannabinoid system was only mapped in the 1990s.",
    "CB1 receptors cluster in the brain; CB2 mostly in the immune system.",
    "Raphael Mechoulam worked out THC's structure in 1964.",
    "CBD was isolated in 1940, decades before anyone understood what it did.",
    "Hemp and cannabis are one species. The line between them is a THC percentage written into law.",
    "Sinsemilla is Spanish for 'without seed' — unpollinated female flower.",
    "Cannabis is dioecious: separate male and female plants, unlike most flowering species.",
    "The word 'canvas' traces back to 'cannabis'. The sails were hemp.",
    "Cannabis pollen drifts for miles, which is why breeders guard their rooms.",
    "China's Pen Ts'ao described cannabis as medicine thousands of years ago.",
    "Hemp seeds are a complete protein, carrying all nine essential amino acids.",
    "420 traces to San Rafael students in 1971 who met at 4:20 to hunt for a rumored crop.",
    "One of the first things ever sold online was reportedly cannabis, over ARPANET around 1971.",
    "Uruguay became the first country to legalize cannabis nationwide, in 2013.",
    "Canada followed with national legalization in 2018.",
    "The entourage effect is the idea that cannabinoids and terpenes do more together than alone.",
    "Terpenes aren't unique to cannabis — they're the aromatic backbone of most plants.",
    "Linalool is the terpene cannabis shares with lavender.",
    "Low temps chase flavor, high temps chase vapor. The terpenes decide.",
    "Most of the resin sits on the flower. Leaves and stems carry very little.",
    "Hash is just separated trichomes, pressed. The technique is centuries old.",
    "Rosin needs nothing but heat and pressure — no solvents at all.",
    "Live resin starts from flash-frozen fresh plant, preserving terpenes that drying would cost.",
    "Cannabinoids are fat-soluble, not water-soluble. That's why edibles need butter or oil.",
    "Edibles hit differently because the liver converts THC into 11-hydroxy-THC.",
    "THC and CBD share the same chemical formula. The atoms are simply arranged differently.",
    "Cannabis has grown alongside humans so long its wild ancestor is hard to pin down.",
    "Terpene profiles explain how a strain feels far better than the indica/sativa label does.",
    "The indica/sativa split describes plant shape better than it describes effects.",
    "Quartz is prized for flavor because it's inert — it adds no taste of its own.",
)


def pick_fact(last: int = -1) -> tuple[int, str]:
    """Return an (index, fact) pair, avoiding an immediate repeat of `last`."""
    choices = [i for i in range(len(FACTS)) if i != last] or list(range(len(FACTS)))
    index = random.choice(choices)
    return index, FACTS[index]
