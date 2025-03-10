from itertools import combinations, chain
from pprint import pprint
from collections import OrderedDict
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

expected_env_vars = {
    "kotoba": "KOTOBA_ID",
    "channel": "ANNOUNCEMENT_CHANNEL_ID",
    "token": "TOKEN"
}
# Keep order as is

# Env vars sanity checks
env_vars_missing = ""
for key, value in expected_env_vars.items():
    if not os.getenv(value):
        env_vars_missing = env_vars_missing + f" {value}"
        # "ENV_VAR1 ENV_VAR2 ..."
        
if len(env_vars_missing) > 0:
    raise KeyError("Certain env vars are not set:" + env_vars_missing)

KOTOBA_ID = os.environ[expected_env_vars["kotoba"]]
ANNOUNCEMENT_CHANNEL_ID = os.environ[expected_env_vars["channel"]]
RANK_NAMES = ['Student', 'Trainee', 'Debut Idol', 'Major Idol', 'passed Prima vocab', 'Prima Idol',
              'passed Divine vocab', 'Divine Idol', 'passed Eternal vocab', 'Eternal Idol', 'GN1', 'GN2']
_DB_NAME = 'quiz_attempts.db'

@dataclass(eq=True)
class QuizSetting:
    font: str
    font_size: int
    foreground: str
    effect: str

    time_limit: int
    additional_answer_time_limit: int

    # TODO(ym): deck ranges
    decks: list
    deck_range: tuple
    score_limit: int

    max_missed: int
    shuffle: bool

    def similar(self, other):
        errors = []
        if (self.foreground is not None and self.foreground != other.foreground) or (self.effect is not None and self.effect != other.effect) or set(self.decks) != set(other.decks) or self.shuffle != other.shuffle:
            errors.append('Quiz settings are different')
        if other.deck_range != self.deck_range:
            errors.append('Deck ranges are different')
        if other.time_limit > self.time_limit or other.additional_answer_time_limit > self.additional_answer_time_limit:
            errors.append("Answer time too long.")
        if other.font_size > self.font_size:
            errors.append("Font size too big.")
        if other.score_limit < self.score_limit:
            errors.append("Score limit too low.")
        if self.font is not None and other.font != self.font:
            errors.append("Font doesn't match.")
        return errors

    # Kind of want this to be autogenerated somehow, maybe using annotations like go?
    @classmethod
    def from_dict(cls, js):
        s = js['settings']
        deck_range = None
        for i in js['decks']:
            if 'startIndex' in i and 'endIndex' in i:
                deck_range = (i['startIndex'], i['endIndex'])
                break
        return cls(decks=[i['shortName'] for i in js['decks']],
                   deck_range=deck_range,
                   font=s['font'],
                   font_size=s['fontSize'],
                   foreground=s['fontColor'],
                   effect=s['effect'] if 'effect' in s else '',
                   time_limit=s['answerTimeLimitInMs'],
                   additional_answer_time_limit=s['additionalAnswerWaitTimeInMs'],
                   score_limit=s['scoreLimit'],
                   max_missed=s['maxMissedQuestions'],
                   shuffle=s['shuffle'] if 'shuffle' in s else s['serverSettings']['shuffle'])

    # I don't like this
    def to_command(self):
        if list(self.decks)[0].startswith("gn"):
            return f"k!quiz {'+'.join(self.decks)} nd {self.score_limit} mmq={self.max_missed}"
        return f"k!quiz {'+'.join(self.decks)}" + (f"({self.deck_range[0]}-{self.deck_range[1]}) " if self.deck_range else " ") + f"{self.score_limit} hardcore nd mmq={self.max_missed} dauq=1 font=5 color={self.foreground} size={self.font_size}" + (f" effect={self.effect}" if self.effect is not None else '')

RankStructure = {
    'Student': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', effect=None, time_limit=16000, additional_answer_time_limit=0, decks=['jpdb1k'], deck_range=(1, 300), score_limit=25, max_missed=10, shuffle=True),
    'Trainee': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', effect=None, time_limit=16000, additional_answer_time_limit=0, decks=['jpdb1k'], deck_range=None, score_limit=50, max_missed=10, shuffle=True),
    'Debut Idol': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=['jpdb2_5k', 'jpdb5k'], deck_range=None, score_limit=50, max_missed=10, shuffle=True),
    'Major Idol': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=['jpdb5k', 'jpdb10k'], deck_range=None, score_limit=50, max_missed=10, shuffle=True),
    'passed Prima vocab': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=['jpdb10k', 'jpdb15k'], deck_range=None, score_limit=50, max_missed=10, shuffle=True),
    'passed Divine vocab': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=['jpdb15k', 'jpdb20k'], deck_range=None, score_limit=50, max_missed=10, shuffle=True),
    'passed Eternal vocab': QuizSetting(font='Eishiikaisho', font_size=100, foreground='#f173ff', effect='antiocr', time_limit=16000, additional_answer_time_limit=0, decks=['jpdb20k', 'jpdb25k'], deck_range=None, score_limit=50, max_missed=10, shuffle=True),
    'GN2': QuizSetting(font='Eishiikaisho', font_size=200, foreground=None, effect=None, time_limit=16000, additional_answer_time_limit=0, decks=['gn2'], deck_range=None, score_limit=20, max_missed=4, shuffle=True),
    'GN1': QuizSetting(font='Eishiikaisho', font_size=200, foreground=None, effect=None, time_limit=16000, additional_answer_time_limit=0, decks=['gn1'], deck_range=None, score_limit=20, max_missed=4, shuffle=True),
}
QuizCommands = [i.to_command() for i in RankStructure.values()]
pprint(QuizCommands, width=100)

DoubleRanks = OrderedDict([
    ('Eternal Idol', [{'passed Eternal vocab', 'GN1'}, {'Divine Idol', 'passed Eternal vocab'}]),
    ('Divine Idol', [{'passed Divine vocab', 'GN1'}, {'Eternal Idol', 'passed Divine vocab'}]),
    ('Prima Idol', [{'passed Prima vocab', 'GN2'}])
])

def get_roles(guild):
    roles = [f for k in RANK_NAMES if (f := get(guild.roles, name=k))]
    if len(roles) != len(RANK_NAMES):
        return {}
    return dict(zip(RANK_NAMES, roles))

async def cooldown(store, channel, member, content):
    unixstamp = store.get_unix()
    await channel.send(f"Please attempt again in <t:{unixstamp}:R> at <t:{unixstamp}>. Any attempts until then will not be counted.")
    await member.send(f'Please attempt again in <t:{unixstamp}:R> at <t:{unixstamp}>. Any attempts on ``{content}`` until then will not be counted.')

async def fail(store, quiz, guild, channel, member):
    if quiz == 'Student': return
    quiz_command =  RankStructure[quiz].to_command()
    store.new_quiz_attempt(member.id, quiz_command, datetime.now(), "FAILED")
    await cooldown(store, channel, member, quiz_command)

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
    async def on_member_update(self, before, after):
        guild_roles = get_roles(before.guild)
        nqr = set(guild_roles.values()) & (set(after.roles) - set(before.roles))
        if len(nqr) == 0:
            return
        elif len(nqr) > 1:
            print("len(nqr) > 1")

        nqr = list(nqr)[0]
        last_quiz, created_at, result = self.store.get_last_attempt(before.id)
        quiz_name = [k for k, v in RankStructure.items() if v.to_command() == last_quiz][0]
        if result != "PASSED" or datetime.now() - datetime.fromisoformat(created_at) > timedelta(minutes=2) or (nqr.name != quiz_name and (nqr.name not in DoubleRanks or quiz_name not in chain(*DoubleRanks[nqr.name]))): # Sanity check
            return

        member = before.guild.get_member(before.id)
        after_roles = set(map(lambda x: x.name, after.roles))
        for k in filter(lambda k: k not in after_roles, DoubleRanks):
            for i in DoubleRanks[k]:
                # on_member_update will get called again
                if after_roles.issuperset(i):
                    return await member.add_roles(guild_roles[k])

        if quiz_name.startswith("passed"):
            quiz_name = quiz_name.split(" ")[1] + " Idol"
        quiz_name += " grammar" if quiz_name.startswith("GN") else " vocab"
        await member.send(f"You passed the {quiz_name} quiz! Your role is now updated.")

        if not nqr.name.startswith("passed") and not nqr.name.startswith("GN"):
            rr = set(before.roles) & set(guild_roles.values()) - {guild_roles['GN1'], guild_roles['GN2']}
            await member.remove_roles(*rr)

            announcement_channel = get(member.guild.channels, name='一般') or member.guild.get_channel(ANNOUNCEMENT_CHANNEL_ID)
            await announcement_channel.send(f"{member.mention} has passed the {quiz_name} quiz and is now a {nqr.name}!")

    @commands.Cog.listener()
    async def on_message(self, message):
        if any(message.content.startswith(i) for i in COMB_CACHE):
            if message.content in QuizCommands:
                if not self.store.get_attempts(message.author.id, message.content):
                    return await message.channel.send("This attempt will be counted!")
                await cooldown(self.store, message.channel, message.author, message.content)
                return await message.author.timeout(utcnow() + timedelta(minutes=2), reason="Invalid quiz attempt")

            await message.channel.send("Wrong quiz command")
            await message.author.timeout(utcnow() + timedelta(minutes=5), reason="Wrong quiz command")
            await message.channel.set_permissions(message.channel.guild.default_role, view_channel=False)
            await message.channel.set_permissions(message.channel.guild.default_role, send_messages=False)
            await asyncio.sleep(300)
            await message.channel.set_permissions(message.channel.guild.default_role, view_channel=True)
            await message.channel.set_permissions(message.channel.guild.default_role, send_messages=True)
            return await message.channel.set_permissions(message.channel.guild.default_role, read_message_history=False)

        if "k!" in message.content and "jpdb" in message.content and "conquest" in message.content:
            await message.channel.send("Wrong quiz command")
            await message.author.timeout(utcnow() + timedelta(minutes=5), reason="Wrong quiz command")
            await message.channel.set_permissions(message.channel.guild.default_role, view_channel=False)
            await message.channel.set_permissions(message.channel.guild.default_role, send_messages=False)
            await asyncio.sleep(300)
            await message.channel.set_permissions(message.channel.guild.default_role, view_channel=True)
            await message.channel.set_permissions(message.channel.guild.default_role, send_messages=True)
            return await message.channel.set_permissions(message.channel.guild.default_role, read_message_history=False)
      
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
                     for k, v in RankStructure.items() if set(v.decks) == decks]
        if len(quiz_cand) == 0:
            return  # Quiz that isn't ranked

        # Match based on the settings
        report_settings = QuizSetting.from_dict(report)
        member = message.guild.get_member(
            int(report['participants'][0]['discordUser']['id']))

        similarity = *sorted([(k, v.similar(report_settings))
                             for k, v in quiz_cand], key=lambda x: len(x[1])),
        if len(similarity[0][1]) > 0:
            await message.channel.send('\n'.join(similarity[0][1]))
            return await fail(self.store, similarity[0][0], message.guild, message.channel, member)

        quiz_name = list(sorted([i for i in similarity if len(
            i[1]) == 0], key=lambda x: -RankStructure[x[0]].score_limit))[0][0]

        if len(report['participants']) > 1:
            await message.channel.send('Too many participants.')
            return await fail(self.store, quiz_name, message.guild, message.channel, member)

        if report['scores'][0]['score'] != RankStructure[quiz_name].score_limit:
            await message.channel.send("Score and limit don't match.")
            return await fail(self.store, quiz_name, message.guild, message.channel, member)

        self.store.new_quiz_attempt(member.id, RankStructure[quiz_name].to_command(), datetime.now(), "PASSED")
        await member.add_roles(guild_roles[quiz_name]) # on_member_update takes on from here


class Bot(commands.Bot):
    async def on_ready(self):
        print('Logged in as:', self.user.name, "ID:", self.user.id)

    async def setup_hook(self):
        self.store = Store(_DB_NAME)
        await self.add_cog(Quiz(self))


if __name__ == '__main__':
    meido = Bot(command_prefix='!', intents=discord.Intents.all())
    meido.run(os.environ[expected_env_vars["token"]])
