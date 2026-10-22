
/usr/lib/x86_64-linux-gnu/libjson-c.so:     file format elf64-x86-64


Disassembly of section .init:

Disassembly of section .plt:

Disassembly of section .plt.got:

Disassembly of section .plt.sec:

Disassembly of section .text:

0000000000008d90 <json_object_to_json_string_length@@JSONC_0.14>:
    8d90:	f3 0f 1e fa          	endbr64
    8d94:	55                   	push   %rbp
    8d95:	48 8d 05 7a 53 00 00 	lea    0x537a(%rip),%rax        # e116 <json_patch_apply@@JSONC_0.17+0x1536>
    8d9c:	48 89 e5             	mov    %rsp,%rbp
    8d9f:	41 55                	push   %r13
    8da1:	41 54                	push   %r12
    8da3:	49 89 d4             	mov    %rdx,%r12
    8da6:	ba 04 00 00 00       	mov    $0x4,%edx
    8dab:	53                   	push   %rbx
    8dac:	48 83 ec 08          	sub    $0x8,%rsp
    8db0:	48 85 ff             	test   %rdi,%rdi
    8db3:	74 32                	je     8de7 <json_object_to_json_string_length@@JSONC_0.14+0x57>
    8db5:	48 89 fb             	mov    %rdi,%rbx
    8db8:	48 8b 7f 10          	mov    0x10(%rdi),%rdi
    8dbc:	41 89 f5             	mov    %esi,%r13d
    8dbf:	48 85 ff             	test   %rdi,%rdi
    8dc2:	74 3c                	je     8e00 <json_object_to_json_string_length@@JSONC_0.14+0x70>
    8dc4:	e8 a7 ff ff ff       	call   8d70 <printbuf_reset@@JSONC_PRIVATE>
    8dc9:	31 d2                	xor    %edx,%edx
    8dcb:	48 8b 73 10          	mov    0x10(%rbx),%rsi
    8dcf:	44 89 e9             	mov    %r13d,%ecx
    8dd2:	48 89 df             	mov    %rbx,%rdi
    8dd5:	ff 53 08             	call   *0x8(%rbx)
    8dd8:	85 c0                	test   %eax,%eax
    8dda:	78 3c                	js     8e18 <json_object_to_json_string_length@@JSONC_0.14+0x88>
    8ddc:	48 8b 43 10          	mov    0x10(%rbx),%rax
    8de0:	48 63 50 08          	movslq 0x8(%rax),%rdx
    8de4:	48 8b 00             	mov    (%rax),%rax
    8de7:	4d 85 e4             	test   %r12,%r12
    8dea:	74 04                	je     8df0 <json_object_to_json_string_length@@JSONC_0.14+0x60>
    8dec:	49 89 14 24          	mov    %rdx,(%r12)
    8df0:	48 83 c4 08          	add    $0x8,%rsp
    8df4:	5b                   	pop    %rbx
    8df5:	41 5c                	pop    %r12
    8df7:	41 5d                	pop    %r13
    8df9:	5d                   	pop    %rbp
    8dfa:	c3                   	ret
    8dfb:	0f 1f 44 00 00       	nopl   0x0(%rax,%rax,1)
    8e00:	e8 1b ed ff ff       	call   7b20 <printbuf_new@@JSONC_PRIVATE>
    8e05:	48 89 43 10          	mov    %rax,0x10(%rbx)
    8e09:	48 89 c7             	mov    %rax,%rdi
    8e0c:	48 85 c0             	test   %rax,%rax
    8e0f:	75 b3                	jne    8dc4 <json_object_to_json_string_length@@JSONC_0.14+0x34>
    8e11:	0f 1f 80 00 00 00 00 	nopl   0x0(%rax)
    8e18:	31 d2                	xor    %edx,%edx
    8e1a:	31 c0                	xor    %eax,%eax
    8e1c:	eb c9                	jmp    8de7 <json_object_to_json_string_length@@JSONC_0.14+0x57>

Disassembly of section .fini:
