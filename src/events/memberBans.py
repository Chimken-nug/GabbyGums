"""
Cog for the on_member_ban and on_member_unban events.
Logs from these event include:
    When a user is banned
    When a user is unbanned

It also (temporarily) handles the on_member_join event for only the following conditions:
    Detects and then sending a warning embed in the case where the joining user has a banned PK account.

Part of the Gabby Gums Discord Logger.
"""

import asyncio
import logging

from datetime import timedelta, datetime
from typing import TYPE_CHECKING, Optional, Dict, List, Union, Tuple, NamedTuple

import discord
from discord.ext import commands

import aiohttp

from embeds import member_ban, member_unban
from utils import get_audit_logs, MissingAuditLogPermissions, split_text
import db

if TYPE_CHECKING:
    from bot import GGBot

log = logging.getLogger(__name__)


class CouldNotConnectToPKAPI(Exception):
    pass


async def get_pk_system_from_userid(user_id: int) -> Optional[Dict]:

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f'https://api.pluralkit.me/v1/a/{user_id}') as r:
                if r.status == 200:  # We received a valid response from the PK API.
                    logging.info(f"User has an associated PK Account linked to thier Discord Account.")

                    # Convert the JSON response to a dict
                    pk_response = await r.json()

                    # Unpack and return.
                    logging.info(f"Got system: {pk_response}")
                    # system_id = pk_response['id']
                    # return system_id
                    return pk_response
                elif r.status == 404:
                    # No PK Account found.
                    log.info("No PK Account found.")
                    return None

    except aiohttp.ClientError as e:
        raise CouldNotConnectToPKAPI  # Really not strictly necessary, but it makes the code a bit nicer I think.


class MemberBans(commands.Cog):
    def __init__(self, bot: 'GGBot'):
        self.bot = bot


    # TODO: Merge this event into the other on-member-join once it's been moved into a cog.
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        event_type = "member_join"
        
        await asyncio.sleep(4)  # Make sure the warning is displayed AFTER the New Member Joined Tab for viability.
        if await db.any_banned_systems(self.bot.db_pool, member.guild.id):  # Make sure that we actually have any banned systems to match against before we hit the PK API.
            # log.info("Guild has banned PK Accounts. Check if current user is banned.")
            try:
                pk_response = await get_pk_system_from_userid(member.id)
                system_id = pk_response['id'] if pk_response is not None else None
            except CouldNotConnectToPKAPI:
                pk_response = None
                system_id = None  # add warning message to embed or maybe retry later?

            if system_id is not None and pk_response is not None:
                banned_users = await db.get_banned_system(self.bot.db_pool, member.guild.id, system_id)
                if len(banned_users) > 0:
                    # log.info(f"This system ({system_id}) is banned. It is advised that this discord account be banned.")

                    log_channel = await self.bot.get_event_or_guild_logging_channel(member.guild.id, event_type)
                    if log_channel is None:
                        # Silently fail if no log channel is configured.
                        return
                    embed = self.banned_pk_account_joined_embed(member, pk_response)
                    await log_channel.send(embed=embed)


    def banned_pk_account_joined_embed(self, member: discord.Member, system_info: Dict) -> discord.Embed:

        if 'name' in system_info and system_info['name'] is not None:
            desc = "**{}** - System ID: **{}**\n\nLinked to: <@{}> - {}#{}".format(system_info['name'], system_info['id'], member.id, member.name, member.discriminator)
            info_value = f"Warning! <@{member.id}> is linked to a Plural Kit system, **{system_info['name']}**, that is banned from this server!\n"

        else:
            desc = "PK System ID: **{}**\n\nLinked to: <@{}> - {}#{}".format(system_info['id'], member.id, member.name, member.discriminator)
            info_value = f"Warning! <@{member.id}> is linked to a Plural Kit system, ID: **{system_info['id']}**, that is banned from this server!\n"

        embed = discord.Embed(description=desc,
                              color=discord.Color.red(), timestamp=datetime.utcnow())

        embed.set_author(name="⚠️WARNING! A Banned Plural Kit System Joined")

        if "avatar_url" in system_info and system_info["avatar_url"] is not None:
            avatar_url = system_info['avatar_url']
            ios_compatible_avatar_url = avatar_url
            embed.set_thumbnail(url=ios_compatible_avatar_url)

        embed.add_field(name="Info:",
                        value=info_value,
                        inline=False)

        embed.set_footer(text="System ID: {}".format(system_info['id']))

        return embed


    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: Union[discord.User, discord.Member]):
        """ User can be either a User (if they were hackbanned) Or a Member () If they were in the guild when banned"""

        # Check to see if the user has an associated Plural Kit Account.
        try:
            pk_response = await get_pk_system_from_userid(user.id)
            system_id = pk_response['id'] if pk_response is not None else None
        except CouldNotConnectToPKAPI:
            pk_response = None
            system_id = None  # add warning message to embed or maybe retry later?

        if system_id is not None and pk_response is not None:
            await db.add_banned_system(self.bot.db_pool, guild.id, system_id, user.id)

        await self.log_member_ban_or_unban(guild, user, "ban", pk_response)


    def hackban_pk_account_embed(self, system_info: Dict) -> discord.Embed:

        if 'name' in system_info and system_info['name'] is not None:
            desc = "{} - System ID: {}".format(system_info['name'], system_info['id'])
            info_value = f"The Plural Kit system, **{system_info['name']}**, has been preemptively banned from this server.\n\n" \
                         f"A warning will be sent if any account linked to this system joins."
        else:
            desc = "System ID: {}".format(system_info['id'])
            info_value = f"The Plural Kit system, ID: **{system_info['id']}**, has been preemptively banned from this server.\n\n" \
                         f"A warning will be sent if any account linked to this system joins."
        embed = discord.Embed(description=desc,
                              color=discord.Color.dark_red(), timestamp=datetime.utcnow())

        embed.set_author(name="Plural Kit System Banned", icon_url="http://i.imgur.com/Imx0Znm.png")

        if "avatar_url" in system_info and system_info["avatar_url"] is not None:
            avatar_url = system_info['avatar_url']
            ios_compatible_avatar_url = avatar_url
            embed.set_thumbnail(url=ios_compatible_avatar_url)

        embed.add_field(name="Info:",
                        value=info_value,
                        inline=False)

        embed.set_footer(text="System ID: {}".format(system_info['id']))

        return embed


    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await self.log_member_ban_or_unban(guild, user, "unban")

        log.info("Checking to see if user has any associated banned discord accounts...")
        try:
            pk_response = await get_pk_system_from_userid(user.id)
            system_id = pk_response['id'] if pk_response is not None else None
        except CouldNotConnectToPKAPI:
            pk_response = None
            system_id = None  # add warning message to embed or maybe retry later?

        if system_id is not None and pk_response is not None:

            banned_users = await db.get_banned_system(self.bot.db_pool, guild.id, system_id)

            if len(banned_users) > 0:
                log.info(f"Found banned accounts: {banned_users}")

                # Remove all the banned entries from the DB.
                log.info(f"Removing system {banned_users[0].system_id} from the banned list.")
                await db.remove_banned_system(self.bot.db_pool, guild.id, banned_users[0].system_id)

                log_channel = await self.bot.get_event_or_guild_logging_channel(guild.id, 'member_unban')
                if log_channel is None:
                    # Silently fail if no log channel is configured.
                    return
                embed = await self.unban_pk_account_embed(user, pk_response, banned_users, False)
                await log_channel.send(embed=embed)


    async def unban_pk_account_embed(self, member: discord.User, system_info: Dict, unbanned_users: List[db.BannedUser], autoban: bool) -> discord.Embed:

        if 'name' in system_info and system_info['name'] is not None:
            desc = "**{}** - System ID: **{}**\n\nLinked to: <@{}> - {}#{}".format(system_info['name'], system_info['id'], member.id, member.name, member.discriminator)
            info_desc = f"The Plural Kit system, **{system_info['name']}**, has been **unbanned** from this server."

        else:
            desc = "System ID: **{}**\n\nLinked to: <@{}> - {}#{}".format(system_info['id'], member.id, member.name, member.discriminator)
            info_desc = f"The Plural Kit system, ID: **{system_info['id']}**, has been **unbanned** from this server."

        embed = discord.Embed(description=desc, color=discord.Color.dark_green(), timestamp=datetime.utcnow())

        embed.set_author(name="Plural Kit System Unbanned", icon_url="https://i.imgur.com/OCcebCO.png")

        if "avatar_url" in system_info and system_info["avatar_url"] is not None:
            avatar_url = system_info['avatar_url']
            # Need to use format other than WebP for image to display on iOS. (I think this is a recent discord bug.)
            ios_compatible_avatar_url = avatar_url
            embed.set_thumbnail(url=ios_compatible_avatar_url)

        unban_msgs = []
        for account in unbanned_users:
            # While I can't see any situation where the member.id WOULD be -1, i'm adding the check just in case and to be extra clear.
            if account.user_id != member.id and account.user_id != -1:
                # This might be a bad idea... it could result in too many API calls...
                unbanned_user = self.bot.get_user(account.user_id)
                if unbanned_user is None:
                    unbanned_user = await self.bot.fetch_user(account.user_id)
                unban_msgs.append(f"<@{account.user_id}> - {unbanned_user.name}#{unbanned_user.discriminator}")

        if len(unban_msgs) == 0:
            autoban_txt = "There appears to be no other Discord accounts linked to this PK Account that are currently banned in this server."
        elif autoban:
            autoban_txt = "The following linked Discord accounts have been automatically unbanned:"
        else:
            autoban_txt = "The following linked Discord accounts are potentially still banned:"

        unban_msg = '\n'.join(unban_msgs)
        info_value = f"{info_desc}\n\n{autoban_txt}\n{unban_msg}"

        info_values = split_text(info_value, 1000)

        for i, txt in enumerate(info_values):
            if i == 0:
                embed.add_field(name="Info:", value=txt, inline=False)
            else:
                embed.add_field(name="\N{Zero Width Space}", value=txt, inline=False)

        embed.set_footer(text="System ID: {}".format(system_info['id']))

        return embed


    async def log_member_ban_or_unban(self, guild: discord.Guild, user: Union[discord.User, discord.Member],
                                      ban_or_unban: str, pk_system_info: Optional[Dict] = None):
        """ If ban, use "ban". if unban, use "unban" """
        log.info(f"User {ban_or_unban} Guild: {guild}, User: {user}")
        await asyncio.sleep(0.5)  # Sleep for a bit to ensure we don't hit a race condition with the Audit Log.
        if ban_or_unban.lower() == "ban":
            audit_action = discord.AuditLogAction.ban
            event_type = "member_ban"
        elif ban_or_unban.lower() == "unban":
            audit_action = discord.AuditLogAction.unban
            event_type = "member_unban"
        else:
            raise ValueError("ban_or_unban must be 'ban' or 'unban' ")

        log_channel = await self.bot.get_event_or_guild_logging_channel(guild.id, event_type)
        if log_channel is None:
            # Silently fail if no log channel is configured.
            return

        try:
            audit_log_entries = await get_audit_logs(guild, audit_action, user, timedelta(seconds=30))
            if len(audit_log_entries) > 0:
                # Assume the latest entry is the correct entry.
                # Todo: Maybe Look at the time data and reject if it's too old? Kinda redundent though since we already filter them all out...
                audit_log = audit_log_entries[0]
                log.info("Got logs")
            else:
                audit_log = None
                log.info("Got NO logs")

        except MissingAuditLogPermissions:
            audit_log = None
            log.info("need perms")

        if ban_or_unban.lower() == "ban":
            embed = member_ban(user, audit_log)
            if pk_system_info is not None:
                embed = self.update_ban_embed_w_pk_account(embed, user, pk_system_info)
        else:
            embed = member_unban(user, audit_log)
        await log_channel.send(embed=embed)


    def update_ban_embed_w_pk_account(self, embed: discord.Embed, member: discord.Member, system_info: Dict) -> discord.Embed:

        if 'name' in system_info and system_info['name'] is not None:
            desc = "**{}** - System ID: **{}**".format(system_info['name'], system_info['id'])
            info_value = f"The Plural Kit system, **{system_info['name']}**, has been banned from this server.\n\n" \
                         f"You will be sent a warning if an account linked to this system joins again."
            name_id_title = f"System Name & ID"
        else:
            desc = "System ID: **{}**".format(system_info['id'])
            info_value = f"The Plural Kit system, ID: **{system_info['id']}**, has been banned from this server.\n\n" \
                         f"You will be sent a warning if an account linked to this system joins again."
            name_id_title = f"System ID"

        embed.add_field(name="\N{Zero Width Space}", value="__**Linked Plural Kit System Banned**__", inline=False)

        embed.add_field(name=name_id_title,
                        value=desc,
                        inline=False)

        embed.add_field(name="Info:",
                        value=info_value,
                        inline=False)

        embed.set_footer(text=f"System ID: {system_info['id']} | User ID: {member.id}")

        return embed


def setup(bot):
    bot.add_cog(MemberBans(bot))
