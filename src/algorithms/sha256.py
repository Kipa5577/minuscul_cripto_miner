import hashlib

# Helper function
def toBinary(a):
    l,m=[],[]
    for i in a:
        l.append(ord(i))
    for i in l:
        m.append(int(bin(i)[2:]))
    return m 

def ror(n,rotations,width):
    mask = (1<< width) - 1
    return ((n>>rotations)|(n<<(width-rotations))) & mask


def mySha256 (input:int):
    # Setp 1 - Pre-Processing
    output = bytearray(input) 
    inputBits = len(output)*8
    output = output + (bytearray([0b1000_0000]))
    while len(output)%64!=56:
        output = output + bytearray(1) 
    #print(f'{len(input)*8:b}'
    output =  output + inputBits.to_bytes(8, 'big')

    # Step 2 Initialize Hash Values
    # 8 hash values that are hard coded
    h0 = 0x6a09e667
    h1 = 0xbb67ae85
    h2 = 0x3c6ef372
    h3 = 0xa54ff53a
    h4 = 0x510e527f
    h5 = 0x9b05688c
    h6 = 0x1f83d9ab
    h7 = 0x5be0cd19
    
    # Step 3 initialize Round Constants
    Klist = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
             0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
             0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
             0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
             0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
             0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
             0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
             0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]


    for block_offset in range(0, len(output), 64):
        block =  output[block_offset:block_offset+64]


        # Step 5 Message Schedule 
        Message_Schedule = [0]*64
        for i in range(16):
            Message_Schedule[i]= int.from_bytes(block[i*4 : (i+1) * 4 ], 'big')
        

        # modification of the indexes at the end 
        for i in range (16,64):
            s0_p1 = ror(Message_Schedule[i-15],7,32)
            s0_p2 = ror(Message_Schedule[i-15],18,32)
            s0_p3 = Message_Schedule[i-15]>>3
            s1_p1 = ror(Message_Schedule[i-2],17,32)
            s1_p2 = ror(Message_Schedule[i-2],19,32)
            s1_p3 = Message_Schedule[i-2]>>10
            s0 = s0_p1 ^ s0_p2 ^ s0_p3
            s1 = s1_p1 ^ s1_p2 ^ s1_p3
            Message_Schedule[i] = (Message_Schedule[i-16] + s0 + Message_Schedule[i-7] + s1)& 0xFFFFFFFF

        # Setp 6 - Compression 
        a=h0
        b=h1
        c=h2
        d=h3
        e=h4
        f=h5
        g=h6
        h=h7

        for i in range(64):
            S1 = (ror(e,6,32)) ^ (ror(e,11,32) ^ (ror(e,25,32)))
            ch = (e & f) ^ ((~e&0xFFFFFFFF) & g)
            temp1 = (h + S1 + ch + Klist[i] + Message_Schedule[i]) & 0xFFFFFFFF
            S0 = (ror(a,2,32)) ^ (ror(a,13,32) ^ (ror(a,22,32)))
            maj = (a & b) ^ (a & c) ^ (b & c)
            temp2 = (S0 + maj) & 0xFFFFFFFF
            h = g
            g = f
            f = e
            e = (d + temp1) & 0xFFFFFFFF
            d = c 
            c = b 
            b = a 
            a = (temp1 + temp2) & 0xFFFFFFFF

        #Step 7 - Modify Final Values

        h0 = (h0 + a)&0xFFFFFFFF
        h1 = (h1 + b)&0xFFFFFFFF
        h2 = (h2 + c)&0xFFFFFFFF
        h3 = (h3 + d)&0xFFFFFFFF
        h4 = (h4 + e)&0xFFFFFFFF
        h5 = (h5 + f)&0xFFFFFFFF
        h6 = (h6 + g)&0xFFFFFFFF
        h7 = (h7 + h)&0xFFFFFFFF

        # Step 8 Concetenate
        output_val = h0.to_bytes(4,'big',signed=False) + h1.to_bytes(4,'big',signed=False) + h2.to_bytes(4,'big',signed=False) + h3.to_bytes(4,'big',signed=False)+ h4.to_bytes(4,'big',signed=False) + h5.to_bytes(4,'big',signed=False) + h6.to_bytes(4,'big',signed=False) + h7.to_bytes(4,'big',signed=False) # append operation  

    return output_val.hex()


if __name__ == '__main__':

    print("My implementation")
    print(mySha256(b"Nobody inspects the spammish repetition"))
    # b"hello world"
    # b"Nobody inspects the spammish repetition"

    print("Baseline implementation")
    m = hashlib.sha256()
    m.update(b"Nobody inspects the spammish repetition")
    print(m.hexdigest())