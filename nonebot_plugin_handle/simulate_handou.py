import pypinyin
from pypinyin import lazy_pinyin, Style
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
idiom_path = os.path.join(script_dir, "resources", "data", "idioms.txt")

rd = open(idiom_path, 'r', encoding='utf-8')
data = rd.read()
rd.close()
idiom_list = data.strip().split("\n")

print(f"总成语数: {len(idiom_list)}")
print("=" * 60)
print("当前线索:")
print("  第1字: zh(声母) -> 存在但位置不对(不在第1位)")
print("  第2字: i(韵母)   -> 位置正确")
print("  第3字: n         -> 存在但位置不对(不在第3位)")
print("  声调: 2 3 4 4")
print("=" * 60)

results = []

for item in idiom_list:
    if len(item) != 4:
        continue

    initials = lazy_pinyin(item, style=Style.INITIALS)
    finals = lazy_pinyin(item, style=Style.FINALS)
    tones_all = lazy_pinyin(item, style=Style.TONE2)

    all_tone_str = "".join(tones_all)
    tone_digits = "".join(filter(str.isdigit, all_tone_str))

    if len(tone_digits) != 4:
        continue

    if tone_digits != "2344":
        continue

    has_zh_wrong_pos = 'zh' in initials[1:]
    has_i_pos2 = 'i' in finals[1]

    n_in_1 = 'n' in initials[0] or 'n' in finals[0]
    n_in_2 = 'n' in initials[1] or 'n' in finals[1]
    n_in_4 = 'n' in initials[3] or 'n' in finals[3]
    has_n_not_pos3 = (n_in_1 or n_in_2 or n_in_4) and not ('n' in initials[2] or 'n' in finals[2])

    if has_zh_wrong_pos and has_i_pos2 and has_n_not_pos3:
        results.append({
            'idiom': item,
            'initials': initials,
            'finals': finals,
            'full_pinyin': tones_all,
            'tones': tone_digits
        })

print(f"\n符合条件的结果数: {len(results)}")
print("=" * 60)

for r in results:
    print(f"  【{r['idiom']}】")
    print(f"    拼音: {' '.join(r['full_pinyin'])}")
    print(f"    声母: {r['initials']}")
    print(f"    韵母: {r['finals']}")
    print(f"    声调: {r['tones']}")
    zh_pos = [str(i+1) for i, x in enumerate(r['initials']) if x == 'zh']
    n_positions = []
    for i in range(4):
        if i != 2 and ('n' in r['initials'][i] or 'n' in r['finals'][i]):
            n_positions.append(str(i+1))
    print(f"    验证: zh在[{','.join(zh_pos)}]位 | i在第2位({r['finals'][1]}) | n在[{','.join(n_positions)}]位")
    print()

if len(results) == 0:
    print("未找到完全匹配的结果！尝试逐步放宽条件排查...\n")

    print("--- 只要求 声调2344 + zh不在第1位 + 第2位有i ---")
    partial = []
    for item in idiom_list:
        if len(item) != 4:
            continue
        initials = lazy_pinyin(item, style=Style.INITIALS)
        finals = lazy_pinyin(item, style=Style.FINALS)
        tones_all = lazy_pinyin(item, style=Style.TONE2)
        all_tone_str = "".join(tones_all)
        tone_digits = "".join(filter(str.isdigit, all_tone_str))
        if len(tone_digits) != 4 or tone_digits != "2344":
            continue
        has_zh_wrong_pos = 'zh' in initials[1:]
        has_i_pos2 = 'i' in finals[1]
        if has_zh_wrong_pos and has_i_pos2:
            partial.append({'idiom': item, 'initials': initials, 'finals': finals, 'full': tones_all})

    print(f"  匹配数: {len(partial)}")
    for r in partial:
        n_info = []
        for i in range(4):
            if 'n' in r['initials'][i] or 'n' in r['finals'][i]:
                n_info.append(f"第{i+1}位({r['initials'][i]}+{r['finals'][i]})")
        n_str = ", ".join(n_info) if n_info else "无n"
        print(f"    {r['idiom']}  {' '.join(r['full'])}  n分布:[{n_str}]")
