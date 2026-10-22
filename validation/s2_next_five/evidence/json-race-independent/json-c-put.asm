
/usr/lib/x86_64-linux-gnu/libjson-c.so:     file format elf64-x86-64


Disassembly of section .init:

Disassembly of section .plt:

Disassembly of section .plt.got:

Disassembly of section .plt.sec:

Disassembly of section .text:

0000000000008f30 <json_object_put@@JSONC_0.14>:
    8f30:	f3 0f 1e fa          	endbr64
    8f34:	48 85 ff             	test   %rdi,%rdi
    8f37:	0f 84 bb 00 00 00    	je     8ff8 <json_object_put@@JSONC_0.14+0xc8>
    8f3d:	55                   	push   %rbp
    8f3e:	48 89 e5             	mov    %rsp,%rbp
    8f41:	53                   	push   %rbx
    8f42:	48 89 fb             	mov    %rdi,%rbx
    8f45:	48 83 ec 08          	sub    $0x8,%rsp
    8f49:	8b 47 04             	mov    0x4(%rdi),%eax
    8f4c:	85 c0                	test   %eax,%eax
    8f4e:	0f 84 a7 00 00 00    	je     8ffb <json_object_put@@JSONC_0.14+0xcb>
    8f54:	83 e8 01             	sub    $0x1,%eax
    8f57:	89 47 04             	mov    %eax,0x4(%rdi)
    8f5a:	75 44                	jne    8fa0 <json_object_put@@JSONC_0.14+0x70>
    8f5c:	48 8b 47 18          	mov    0x18(%rdi),%rax
    8f60:	48 85 c0             	test   %rax,%rax
    8f63:	74 06                	je     8f6b <json_object_put@@JSONC_0.14+0x3b>
    8f65:	48 8b 77 20          	mov    0x20(%rdi),%rsi
    8f69:	ff d0                	call   *%rax
    8f6b:	8b 03                	mov    (%rbx),%eax
    8f6d:	83 f8 05             	cmp    $0x5,%eax
    8f70:	74 5e                	je     8fd0 <json_object_put@@JSONC_0.14+0xa0>
    8f72:	83 f8 06             	cmp    $0x6,%eax
    8f75:	74 69                	je     8fe0 <json_object_put@@JSONC_0.14+0xb0>
    8f77:	83 f8 04             	cmp    $0x4,%eax
    8f7a:	74 34                	je     8fb0 <json_object_put@@JSONC_0.14+0x80>
    8f7c:	48 8b 7b 10          	mov    0x10(%rbx),%rdi
    8f80:	e8 fb fe ff ff       	call   8e80 <printbuf_free@@JSONC_PRIVATE>
    8f85:	48 89 df             	mov    %rbx,%rdi
    8f88:	e8 a3 b3 ff ff       	call   4330 <free@plt>
    8f8d:	48 8b 5d f8          	mov    -0x8(%rbp),%rbx
    8f91:	b8 01 00 00 00       	mov    $0x1,%eax
    8f96:	c9                   	leave
    8f97:	c3                   	ret
    8f98:	0f 1f 84 00 00 00 00 	nopl   0x0(%rax,%rax,1)
    8f9f:	00 
    8fa0:	48 8b 5d f8          	mov    -0x8(%rbp),%rbx
    8fa4:	31 c0                	xor    %eax,%eax
    8fa6:	c9                   	leave
    8fa7:	c3                   	ret
    8fa8:	0f 1f 84 00 00 00 00 	nopl   0x0(%rax,%rax,1)
    8faf:	00 
    8fb0:	48 8b 7b 28          	mov    0x28(%rbx),%rdi
    8fb4:	e8 87 df ff ff       	call   6f40 <lh_table_free@@JSONC_PRIVATE>
    8fb9:	48 8b 7b 10          	mov    0x10(%rbx),%rdi
    8fbd:	e8 be fe ff ff       	call   8e80 <printbuf_free@@JSONC_PRIVATE>
    8fc2:	48 89 df             	mov    %rbx,%rdi
    8fc5:	e8 66 b3 ff ff       	call   4330 <free@plt>
    8fca:	eb c1                	jmp    8f8d <json_object_put@@JSONC_0.14+0x5d>
    8fcc:	0f 1f 40 00          	nopl   0x0(%rax)
    8fd0:	48 8b 7b 28          	mov    0x28(%rbx),%rdi
    8fd4:	e8 07 bd ff ff       	call   4ce0 <array_list_free@@JSONC_PRIVATE>
    8fd9:	eb a1                	jmp    8f7c <json_object_put@@JSONC_0.14+0x4c>
    8fdb:	0f 1f 44 00 00       	nopl   0x0(%rax,%rax,1)
    8fe0:	48 83 7b 28 00       	cmpq   $0x0,0x28(%rbx)
    8fe5:	79 95                	jns    8f7c <json_object_put@@JSONC_0.14+0x4c>
    8fe7:	48 8b 7b 30          	mov    0x30(%rbx),%rdi
    8feb:	e8 40 b3 ff ff       	call   4330 <free@plt>
    8ff0:	eb 8a                	jmp    8f7c <json_object_put@@JSONC_0.14+0x4c>
    8ff2:	66 0f 1f 44 00 00    	nopw   0x0(%rax,%rax,1)
    8ff8:	31 c0                	xor    %eax,%eax
    8ffa:	c3                   	ret
    8ffb:	48 8d 0d 3e 63 00 00 	lea    0x633e(%rip),%rcx        # f340 <json_patch_apply@@JSONC_0.17+0x2760>
    9002:	ba 18 01 00 00       	mov    $0x118,%edx
    9007:	48 8d 35 15 50 00 00 	lea    0x5015(%rip),%rsi        # e023 <json_patch_apply@@JSONC_0.17+0x1443>
    900e:	48 8d 3d 10 51 00 00 	lea    0x5110(%rip),%rdi        # e125 <json_patch_apply@@JSONC_0.17+0x1545>
    9015:	e8 26 b4 ff ff       	call   4440 <__assert_fail@plt>

Disassembly of section .fini:
