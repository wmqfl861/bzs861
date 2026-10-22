
/usr/lib/x86_64-linux-gnu/libjson-c.so:     file format elf64-x86-64


Disassembly of section .init:

Disassembly of section .plt:

Disassembly of section .plt.got:

Disassembly of section .plt.sec:

Disassembly of section .text:

0000000000005470 <json_object_get@@JSONC_0.14>:
    5470:	f3 0f 1e fa          	endbr64
    5474:	48 89 f8             	mov    %rdi,%rax
    5477:	48 85 ff             	test   %rdi,%rdi
    547a:	74 0e                	je     548a <json_object_get@@JSONC_0.14+0x1a>
    547c:	8b 57 04             	mov    0x4(%rdi),%edx
    547f:	83 fa ff             	cmp    $0xffffffff,%edx
    5482:	74 07                	je     548b <json_object_get@@JSONC_0.14+0x1b>
    5484:	83 c2 01             	add    $0x1,%edx
    5487:	89 57 04             	mov    %edx,0x4(%rdi)
    548a:	c3                   	ret
    548b:	55                   	push   %rbp
    548c:	48 8d 0d bd 9e 00 00 	lea    0x9ebd(%rip),%rcx        # f350 <json_patch_apply@@JSONC_0.17+0x2770>
    5493:	ba 05 01 00 00       	mov    $0x105,%edx
    5498:	48 8d 35 84 8b 00 00 	lea    0x8b84(%rip),%rsi        # e023 <json_patch_apply@@JSONC_0.17+0x1443>
    549f:	48 8d 3d 8d 8b 00 00 	lea    0x8b8d(%rip),%rdi        # e033 <json_patch_apply@@JSONC_0.17+0x1453>
    54a6:	48 89 e5             	mov    %rsp,%rbp
    54a9:	e8 92 ef ff ff       	call   4440 <__assert_fail@plt>

Disassembly of section .fini:
