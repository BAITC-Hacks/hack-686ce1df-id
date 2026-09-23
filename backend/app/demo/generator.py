"""Reproducible synthetic people and transactions; no external personal data."""

import random

from .models import DemoSource, InputQuality, SourceGroup, SourceNode, SourceTransfer, money_text, source_quality

GENERATOR_VERSION = "1"
_FIRST_NAMES = (
    ("Александр", "Тимур", "Данияр", "Алихан", "Максим", "Руслан", "Дмитрий", "Никита", "Илья", "Сергей",
     "Ерлан", "Артём", "Андрей", "Роман", "Михаил", "Нурлан", "Арман", "Адиль", "Азамат", "Денис"),
    ("Айдана", "Аружан", "Мария", "Елена", "Анна", "Асем", "Жанна", "Мадина", "Сауле", "Алина",
     "Камила", "Зарина", "Виктория", "Дарья", "Ольга", "Диана", "Анастасия", "Аяулым", "Индира", "София"),
)
_SURNAMES = (
    ("Садыков", "Иванов", "Касымов", "Ахметов", "Петров", "Нурланов", "Ким", "Сериков", "Смирнов", "Омаров",
     "Волков", "Тлеубердин", "Исаев", "Жумабаев", "Соколов", "Абдрахманов", "Орлов", "Мусин", "Фёдоров", "Беков",
     "Попов", "Сулейменов", "Ли", "Каримов", "Белов", "Тарасов", "Алиев", "Рахимов", "Досанов", "Козлов"),
    ("Садыкова", "Иванова", "Касымова", "Ахметова", "Петрова", "Нурланова", "Ким", "Серикова", "Смирнова", "Омарова",
     "Волкова", "Тлеубердина", "Исаева", "Жумабаева", "Соколова", "Абдрахманова", "Орлова", "Мусина", "Фёдорова", "Бекова",
     "Попова", "Сулейменова", "Ли", "Каримова", "Белова", "Тарасова", "Алиева", "Рахимова", "Досанова", "Козлова"),
)
_EXAMPLE_NAMES = {"100001": "Айдана Садыкова", "100010": "Александр Иванов", "100020": "Тимур Ахметов",
                  "100030": "Аружан Касымова", "100040": "Мария Петрова", "900001": "Данияр Нурланов"}


def generate_source(seed: int = 20260923) -> DemoSource:
    rng = random.Random(seed)
    main = [str(100000 + n) for n in range(1, 319)] + ["0007", "7"]
    components = [main]
    for index, size in enumerate((32, 28, 26, 24, 22, 20, 20), start=2):
        components.append([f"{index}00{n:03d}" for n in range(1, size + 1)])
    components.extend([[f"90000{n}"] for n in range(1, 9)])
    groups = tuple(SourceGroup(id=f"group-{index + 1:02d}", name=("Основная сеть" if index == 0 else f"Сценарий {index + 1}"))
                   for index in range(len(components)))
    nodes = []
    protected = {"100001", "100010", "100020", "100030", "100040"}
    for component, group in zip(components, groups):
        for gid in component:
            index = len(nodes)
            full = gid in protected or index % 5 < 3
            quality = InputQuality(is_seed=index % 47 == 0, hop_depth=index % 4,
                                   outbound_censored=False if full else (True if index % 2 else None),
                                   inbound_incomplete=False if full else (None if index % 3 else True))
            gender = rng.randrange(2)
            name = rng.choice(_FIRST_NAMES[gender]) + " " + rng.choice(_SURNAMES[gender])
            name = _EXAMPLE_NAMES.get(gid, name)
            if gid in ("0007", "7"):
                name = "Александр Иванов"
            nodes.append(SourceNode(gid=gid, display_name=name, group_id=group.id, quality=source_quality(quality)))
    transfers = []

    def add(sender: str, receiver: str, tiyn: int | None = None) -> None:
        transfers.append(SourceTransfer(id=f"tx-{len(transfers) + 1:06d}", source=sender, target=receiver,
                                         amount_kzt=money_text(tiyn if tiyn is not None else rng.randint(500_000, 250_000_000)),
                                         date=f"2026-07-{rng.randint(1, 31):02d}"))

    background = [gid for gid in main if gid not in protected]
    # Connect sparse planted motifs, then repeat events along those channels.
    # Unrestricted random pairs would make almost everyone a coordinator.
    ordinary_components = [background] + components[1:8]
    background_pairs = []
    for component in ordinary_components:
        scale = rng.randint(2_000_000, 50_000_000)

        def channel(left, right):
            background_pairs.append((left, right, scale))
            add(left, right, scale)

        blocks = [component[start:start + 8] for start in range(0, len(component), 8)]
        for index, block in enumerate(blocks):
            hub, *leaves = block
            next_hub = blocks[index + 1][0] if index + 1 < len(blocks) else None
            mode = index % 4
            if mode == 3:
                for left, right in zip(block, block[1:]):
                    channel(left, right)
                if next_hub:
                    channel(block[-1], next_hub)
            else:
                for leaf_index, leaf in enumerate(leaves):
                    incoming = mode == 1 or (mode == 0 and leaf_index < 3)
                    channel(leaf, hub) if incoming else channel(hub, leaf)
                if next_hub:
                    channel(hub, next_hub)
    for gid in background[:3]:
        add(gid, "100001", 25_000_000)
        add("100001", gid, 20_000_000)
        add(gid, "100010", 50_000_000)
    add("100010", "100020", 10_000_000)
    add("100020", "100030", 10_000_000)
    for gid in background[3:6]:
        add("100030", gid, 10_000_000)
    add(background[0], "100040", 40_000_000)
    # Repeated pair and reciprocal direction are intentional, with distinct IDs.
    add("0007", "7", 1_234_567)
    add("0007", "7", 2_345_678)
    add("7", "0007", 3_456_789)
    rng.shuffle(background_pairs)
    index = 0
    while len(transfers) < 6000:
        sender, receiver, scale = background_pairs[index % len(background_pairs)]
        add(sender, receiver, scale + rng.randint(-scale // 20, scale // 20))
        index += 1
    return DemoSource(seed=seed, nodes=tuple(nodes), transfers=tuple(transfers), groups=groups)
