from itertools import combinations
from functools import cache

from pprint import pprint
from re import search
from dataclasses import dataclass

import os
import requests
from asyncio import gather
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from discord.utils import get, utcnow

from role_db import Store

KOTOBA_ID = 251239170058616833
ANNOUNCEMENT_CHANNEL_ID = 617136489482027059
RANK_NAMES = ['Student', 'Trainee', 'Debut Idol', 'Major Idol', 'passed Prima vocab', 'Prima Idol',
              'passed Divine vocab', 'Divine Idol', 'passed Eternal vocab', 'Eternal Idol', 'GN1', 'GN2']
_DB_NAME = 'quiz_attempts.db'

@dataclass(eq=True, frozen=True)
class QuizSetting:
    font: str
    font_size: int
    foreground: str
    background: str
    effect: str

    time_limit: int
    additional_answer_time_limit: int

    # TODO(ym): deck ranges
    decks: frozenset[str]
    score_limit: int

    max_missed: int
    shuffle: bool

    def similar(self, other):
        errors = []
        if self.foreground != other.foreground or self.background != other.background or self.effect != other.effect or self.decks != other.decks or self.shuffle != other.shuffle:
            errors.append('Quiz settings are different')
        if other.additional_answer_time_limit > self.additional_answer_time_limit:
            errors.append("Answer time too long.")
        if other.font_size > self.font_size:
            errors.append("Font size too big.")
        if other.score_limit < self.score_limit:
            errors.append("Score limit too low.")
        if other.font != self.font:
            errors.append("Font doesn't match.")
        return errors

    # Kind of want this to be autogenerated somehow, maybe using annotations like go?
    @classmethod
    def from_dict(cls, js):
        s = js['settings']
        return cls(decks=frozenset([i['shortName'] for i in js['decks']]),
                   font=s['font'],
                   font_size=s['fontSize'],
                   foreground=s['fontColor'],
                   background=s['backgroundColor'],
                   effect=s['effect'] if 'effect' in s else '',
                   time_limit=s['answerTimeLimitInMs'],
                   additional_answer_time_limit=s['additionalAnswerWaitTimeInMs'],
                   score_limit=s['scoreLimit'],
                   max_missed=s['maxMissedQuestions'],
                   shuffle=s['shuffle'] if 'shuffle' in s else s['serverSettings']['shuffle'])

    # I don't like this
    @cache
    def to_command(self):
        if list(self.decks)[0].startswith("gn"):
            return f"k!quiz {'+'.join(self.decks)} nd {self.score_limit} mmq={self.max_missed}"
        return f"k!quiz {'+'.join(self.decks)} {self.score_limit} hardcore nd mmq={self.max_missed} dauq=1 font=5 color={self.foreground} size={self.font_size}" + (f" effect={self.effect}" if self.effect != '' else '')

RankStructure = {
    'Student': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', background='rgb(255, 255, 255)', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=frozenset({'jpdb1k'}), score_limit=25, max_missed=10, shuffle=True),
    'Trainee': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', background='rgb(255, 255, 255)', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=frozenset({'jpdb1k'}), score_limit=50, max_missed=10, shuffle=True),
    'Debut Idol': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', background='rgb(255, 255, 255)', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=frozenset({'jpdb2_5k', 'jpdb5k'}), score_limit=50, max_missed=10, shuffle=True),
    'Major Idol': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', background='rgb(255, 255, 255)', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=frozenset({'jpdb5k', 'jpdb10k'}), score_limit=50, max_missed=10, shuffle=True),
    'passed Prima vocab': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', background='rgb(255, 255, 255)', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=frozenset({'jpdb10k', 'jpdb15k'}), score_limit=50, max_missed=10, shuffle=True),
    'passed Divine vocab': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', background='rgb(255, 255, 255)', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=frozenset({'jpdb15k', 'jpdb20k'}), score_limit=50, max_missed=10, shuffle=True),
    'passed Eternal vocab': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', background='rgb(255, 255, 255)', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=frozenset({'jpdb20k', 'jpdb25k'}), score_limit=50, max_missed=10, shuffle=True),
    'GN2': QuizSetting(font='Eishiikaisho', font_size=200, foreground='#f173ff', background='rgb(255, 255, 255)', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=frozenset({'gn2'}), score_limit=20, max_missed=4, shuffle=True),
    'GN1': QuizSetting(font='Eishiikaisho', font_size=200, foreground='#f173ff', background='rgb(255, 255, 255)', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=frozenset({'gn1'}), score_limit=20, max_missed=4, shuffle=True),
}
QuizCommands = [i.to_command() for i in RankStructure.values()]
pprint(QuizCommands, width=100)

DoubleRanks = [
    ('Eternal Idol', [{'passed Eternal vocab', 'GN1'}, {'Divine Idol', 'passed Eternal vocab'}]),
    ('Divine Idol', [{'passed Divine vocab', 'GN1'}, {'Eternal Idol', 'passed Divine vocab'}]),
    ('Prima Idol', [{'passed Prima vocab', 'GN2'}])
]

def get_roles(guild):
    roles = [f for k in RANK_NAMES if (f := get(guild.roles, name=k))]
    if len(roles) != len(RANK_NAMES):
        return []
    return dict(zip(RANK_NAMES, roles))

async def fail(store, quiz, guild, channel, member_id):
    if quiz == 'Student':
        return
    quizcommand = RankStructure[quiz].to_command()
    store.new_quiz_attempt(member_id, quizcommand, datetime.now(), "FAILED")
    unixstamp = store.get_unix()
    member = guild.get_member(member_id)
    await channel.send(f"Please attempt again in <t:{int(unixstamp)}:R> at <t:{unixstamp}>. Any attempts until then will not be counted.")
    await member.send(f'Please attempt again in <t:{int(unixstamp)}:R> at <t:{unixstamp}>. Any attempts on ``{quizcommand}`` until then will not be counted.')

all_decks = {x for _, v in RankStructure.items() for x in v.decks}
# This is kind of big, maybe we should make it range(1, 4) max?
# Currently it is 511 elements, with 4 it would be 129, 3 -> 45
COMB_CACHE = [f"k!quiz {'+'.join(x)}" for i in range(1, len(all_decks)+1)
              for x in combinations(all_decks, i)]

class Quiz(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.store = self.bot.store

    @commands.Cog.listener()
    async def on_message(self, message):
        if any(message.content.startswith(i) for i in COMB_CACHE):
            if message.content in QuizCommands:
                logs = self.store.get_attempts(
                    message.author.id, message.content)
                if logs[0][0] == 0:
                    return await message.channel.send("This attempt will be counted!")
                unixstamp = self.store.get_unix()
                await message.channel.send(f"Please attempt again in <t:{int(unixstamp)}:R> at <t:{unixstamp}>. Any attempts until then will not be counted.")
                await message.guild.get_member(message.author.id).send(f'Please attempt again in <t:{int(unixstamp)}:R> at <t:{unixstamp}>. Any attempts on ``{message.content}`` until then will not be counted.')
                return await message.author.timeout(utcnow() + timedelta(minutes=2), reason="Invalid quiz attempt")

            await message.channel.send("Wrong quiz command")
            return await message.author.timeout(utcnow() + timedelta(minutes=5), reason="Wrong quiz command")

        if not message.embeds or message.author.id != KOTOBA_ID:
            return

        cand = [field.value for embed in message.embeds for field in embed.fields if 'View a report' in field.value]
        if len(cand) == 0:
            return

        guild_roles = get_roles(message.guild)
        if len(guild_roles) == 0:
            return

        url = 'https://kotobaweb.com/api/game_reports/' + \
            search(r'game_reports/([^)]*)\)', cand[0]).group(1)
        report = requests.get(url).json()

        # Match based on the deck
        decks = {i['shortName'] for i in report['decks']}
        quiz_cand = [(k, v)
                     for k, v in RankStructure.items() if v.decks == decks]
        if len(quiz_cand) == 0:
            return  # Quiz that isn't ranked

        # Match based on the settings
        report_settings = QuizSetting.from_dict(report)
        similarity = *sorted([(k, v.similar(report_settings))
                             for k, v in quiz_cand], key=lambda x: len(x[1])),
        if len(similarity[0][1]) > 0:
            await message.channel.send('\n'.join(similarity[0][1]))
            return await fail(self.store, similarity[0][0], message.guild, message.channel, int(report['participants'][0]['discordUser']['id']))

        # Sort based on the distance to score_limit, hack to fix student getting assigned instead of trainee
        similarity = *sorted([i for i in similarity if len(i[1]) == 0],
                             key=lambda x: -RankStructure[x[0]].score_limit),

        quiz_name = similarity[0][0]

        if len(report['participants']) > 1:
            await message.channel.send('Too many participants.')
            # This poor guy
            return await fail(self.store, quiz_name, message.guild, message.channel, int(report['participants'][0]['discordUser']['id']))

        member = message.guild.get_member(
            int(report['participants'][0]['discordUser']['id']))

        before = frozenset(member.roles)
        print(before)

        if report['scores'][0]['score'] == RankStructure[quiz_name].score_limit:
            await member.add_roles(guild_roles[quiz_name])
        else:
            await message.channel.send("Score and limit don't match.")
            return await fail(self.store, quiz_name, message.guild, message.channel, member.id)

        async def add_double_ranks(member):
            member_roles = set(map(lambda x: x.name, member.roles))
            for k, v in DoubleRanks:
                for i in v:
                    if member_roles.issuperset(i):
                        for f in (i | {'passed Prima vocab', 'passed Divine vocab', 'passed Eternal vocab'}):
                            if f == 'GN1' or f == 'GN2':
                                continue
                            await member.remove_roles(guild_roles[f])
                        return await member.add_roles(guild_roles[k])
        await add_double_ranks(member)

        print(before)
        print(member.roles)
        new_role = list(frozenset(member.roles) - before)
        print(new_role)

        # TODO(YM): check why this happens
        # Maybe busy loop until it gets updated?
        if len(new_role) == 0:
            print("len(new_role) == 0???")
            return

        new_role = new_role[0]
        self.store.save_role_info(member.id, new_role.id, datetime.now())

        if quiz_name.startswith("passed"):
            z = quiz_name.split(" ")
            quiz_name = f"{z[1]} Idol {z[2]}"
        await message.channel.send(f"You passed the {quiz_name} quiz! Your role is now updated.")

        announcement_channel = get(message.guild.channels, name='general')
        if not announcement_channel:
            announcement_channel = message.guild.get_channel(
                ANNOUNCEMENT_CHANNEL_ID)
        if not (quiz_name.endswith("vocab") and new_role.name.endswith("vocab")) and not (quiz_name[:2] == new_role.name[:2] == "GN"):
            await announcement_channel.send(f"{member.mention} has passed the {quiz_name} quiz and is now {new_role.mention}!")

class Bot(commands.Bot):
    async def on_ready(self):
        print('Logged in as:', self.user.name, "ID:", self.user.id)

    async def setup_hook(self):
        self.store = Store(_DB_NAME)
        await self.add_cog(Quiz(self))

if __name__ == '__main__':
    meido = Bot(command_prefix='!', intents=discord.Intents.all())
    meido.run(os.environ['TOKEN'])
