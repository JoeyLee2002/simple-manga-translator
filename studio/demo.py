from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from .storage import Project


def create_demo(root):
    root = Path(root)
    if (root / 'project.json').exists():
        return Project(root)
    p = Project(root, create=True, name='示例 · 小机器人与星光')
    ft = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', 26)
    scenarios = [
        [('I AM NOT\nYOUR ENEMY.', '我不是你的敌人。'),
         ('WE HAVE\n300 YEARS\nTO REMEMBER.', '我们有300年的时光，去记住这一切。')],
        [('LET US\nPROTECT\nTHIS WORLD.', '一起守护这个世界吧。'),
         ('ONE SMALL\nSTEP.\nA NEW START.', '迈出一小步。\n迎接新的开始。')],
    ]
    for n, content in enumerate(scenarios, 1):
        im = Image.new('RGB', (840, 1180), '#f1f5f9')
        dr = ImageDraw.Draw(im)
        dr.rectangle((24, 24, 816, 1156), outline='#142638', width=5)
        for j, (english, zh) in enumerate(content):
            top = 60 + j*550
            dr.rectangle((40, top, 800, top+510), fill='white', outline='#142638', width=4)
            dr.ellipse((90, top+230, 310, top+440), fill='#d2e9e6', outline='#142638', width=4)
            dr.rectangle((130, top+290, 270, top+400), outline='#142638', width=5)
            dr.ellipse((155, top+315, 178, top+339), fill='#142638')
            dr.ellipse((218, top+315, 241, top+339), fill='#142638')
            dr.line((178, top+369, 219, top+369), fill='#142638', width=4)
            dr.rounded_rectangle((400, top+50, 744, top+272), radius=45, fill='white', outline='#142638', width=4)
            dr.multiline_text((572, top+156), english, font=ft, fill='#142638', anchor='mm', align='center', spacing=9)
        d = p.add_image(im, '第01话', f'示例{n:02d}')
        groups = []
        for j, (english, zh) in enumerate(content):
            top = 60+j*550
            box = [430, top+83, 714, top+238]
            groups.append({'id': j+1, 'box': box, 'text': english.replace('\n', ' '), 'zh': zh,
                           'confidence': 1, 'role': 'dialogue', 'lines': [{'box': box, 'text': english, 'confidence': 1}]})
        d.update(groups=groups, ocr_complete=True, secondary_complete=True, status='translated')
        p.save_page(d)
    return p
