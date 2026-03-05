# IP6000 AVP Series MCU API Summary

## 1. Introduction

This document describes the network API for the IP6000 AVP Series, based on Semtech AVP SDVoE. It includes commands for discovering devices, configuring them, and gathering status or statistics, using UDP-based classic and TLV-style messages.

Supported product lines include:
- IP6000AS (AVP2000/T)
- IP6000LAS (AVP1000)
- IP6000W (Wall plate)

## 2. API Overview

APIs use UDP messages with the following common fields:

| Field | Type | Size | Description |
|-------|------|------|-------------|
| Magic_number | UInt32 | 4 | Identifier: `0x11223344` |
| Msg_ID | UInt32 | 4 | Message sequence number |
| Protocol_Version | Char | 1 | Extended feature support indicator |
| Command | Char | 1 | Message function |
| Arg_1, Arg_2... | - | varies | Command-specific arguments |

## 2.2 Broadcast API - Discover Units

### Discover Unit - Request

| Field | Type | Size | Description |
|-------|------|------|-------------|
| Magic_number | UInt32 | 4 | `0x11223344` |
| Msg_ID | UInt32 | 4 | Sequence number |
| Protocol_Version | Char | 1 | 0 |
| Command | Char | 1 | 0 |

- Sent by controller to broadcast address `255.255.255.255:6239` from `6240`.

### Discover Unit - Response

Returns unit info including MACs, board type, USB config, and firmware version.

| Field | Type | Size | Description |
|-------|------|------|-------------|
| MCU_MAC | UChar | 6 | External MCU MAC |
| AVP_MAC | UChar | 6 | AVP chip MAC |
| Dante_MAC | UChar | 6 | Dante module MAC (or 0s) |
| Board type | Char | 1 | 0: TX, 1: RX, 2: TRX |
| USB Config | Char | 1 | 0-6 (defines USB module behavior) |
| MCU Version | Char | 32 | Firmware string |

... (More content continues for unicast, config, TLV commands, netstat, etc.)
